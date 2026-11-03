"""Prometheus metric rendering tests."""

from imeeting.ops import (
    disk_gauges,
    observe_request,
    prometheus_text,
    reset_metrics,
)


def test_request_metrics_normalize_ids_and_render_histogram() -> None:
    reset_metrics()
    observe_request("GET", "/api/v1/meetings/42", 200, 0.003)
    observe_request("GET", "/api/v1/meetings/43", 200, 0.02)

    output = prometheus_text()

    path = 'path="/api/v1/meetings/{id}"'
    assert f'meeting_http_requests_total{{method="GET",{path},status="200"}} 2' in output
    assert f'meeting_http_request_duration_seconds_bucket{{method="GET",{path},le="0.005"}} 1' in output
    assert f'meeting_http_request_duration_seconds_bucket{{method="GET",{path},le="0.01"}} 1' in output
    assert f'meeting_http_request_duration_seconds_bucket{{method="GET",{path},le="0.025"}} 2' in output
    assert f'meeting_http_request_duration_seconds_bucket{{method="GET",{path},le="+Inf"}} 2' in output
    assert f'meeting_http_request_duration_seconds_count{{method="GET",{path}}} 2' in output


def test_share_tokens_do_not_become_metric_labels() -> None:
    reset_metrics()
    observe_request("GET", "/api/v1/share/secret-token/open", 401, 0.01)

    output = prometheus_text()

    assert "secret-token" not in output
    assert 'path="/api/v1/share/{token}/open"' in output


def test_capacity_and_job_gauges_are_rendered(tmp_path) -> None:
    reset_metrics()

    output = prometheus_text(
        extra={"meeting_jobs": {"pending": 3, "running": 1}},
        gauges={
            **disk_gauges(tmp_path),
            "meeting_job_oldest_pending_seconds": 42.5,
        },
    )

    assert "meeting_disk_free_bytes " in output
    assert "meeting_disk_used_ratio " in output
    assert "meeting_job_oldest_pending_seconds 42.500000" in output
    assert 'meeting_jobs{status="pending"} 3' in output


def test_metrics_endpoint_exposes_capacity_and_cost(client) -> None:
    response = client.get("/metrics")

    assert response.status_code == 200
    assert "meeting_disk_used_ratio " in response.text
    assert "meeting_job_oldest_pending_seconds " in response.text
    assert "meeting_llm_cost_usd " in response.text
    assert "meeting_models_loaded " in response.text


def test_metrics_render_db_pool_gauges(client) -> None:
    """S-7 的池指标名字必须稳定（IM-PLN-004 §4.1）。

    改名字不会让任何功能测试失败，但会让告警规则静默失效——上线才发现
    ``meeting_db_pool_in_use`` 有值、自己盯的旧名字永远是空图。
    """
    response = client.get("/metrics")

    assert response.status_code == 200
    assert "meeting_db_pool_in_use " in response.text
    assert "meeting_db_pool_size " in response.text
    assert "meeting_db_pool_overflow_limit " in response.text


def test_metrics_render_writer_stats_when_writer_exists(client, monkeypatch) -> None:
    """C-3：单写者的队列深度与计数必须能进 /metrics。"""
    from imeeting.application import session_writer as module

    class _FakeWriter:
        def stats(self) -> dict[str, int]:
            return {"queue_depth": 3, "executed": 7, "dropped": 1, "errors": 0}

        def close(self, timeout: float = 10.0) -> None:  # 生命周期收尾会调它
            return None

    monkeypatch.setattr(module, "_writer", _FakeWriter())

    text = client.get("/metrics").text

    assert "meeting_session_writer_queue_depth 3.000000" in text
    assert "meeting_session_writer_executed_total 7" in text
    assert "meeting_session_writer_dropped_total 1" in text
    assert "meeting_session_writer_errors_total 0" in text


def test_oversized_audio_frame_is_counted(client) -> None:
    """S-5：超限帧必须既被丢弃、又被计数。

    只打日志的版本在生产里等于没有观测：日志被刷屏、被采样、被打包丢掉，
    都看不出"有人在拿 16MB 帧打我们"。
    """
    from imeeting.api.ws.stream import _MAX_FRAME_BYTES, _decode_pcm
    from imeeting.ops import dropped_frames_total

    before = dropped_frames_total()
    assert _decode_pcm(b"\x00" * (_MAX_FRAME_BYTES + 4)) is None
    assert dropped_frames_total() == before + 1
    assert "meeting_ws_frame_dropped_total " in client.get("/metrics").text


def test_process_thread_gauge_is_exposed(client) -> None:
    """S-2/P-4：线程总数必须可见，否则线程泄漏只能靠肉眼看进程。"""
    response = client.get("/metrics")

    assert "meeting_process_threads " in response.text
