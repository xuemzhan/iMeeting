"""Prometheus metric rendering tests."""

from imeeting.ops import observe_request, prometheus_text, reset_metrics


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
