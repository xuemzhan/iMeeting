"""FunASR 模型配置「真的被用上了」的回归用例。

背景：把配置项加进 ``CONFIG_SCHEMA`` 只是让它出现在设置页上。如果代码里
没有真的去读它，管理员改完点保存会提示成功、界面也显示新值，实际却毫无
变化——表现为「改了没反应」。这种问题在界面上完全看不出来，只能靠测试锁住。

另外锁住一类易错写法：``get_config(key) or 默认值``。当 ``0`` 是合法值
（本项目里 ``vad.min_speech_ms`` / ``spk.min_speech_ms`` / ``spk.min_audio_ms``
都用 0 表示「不限制」）时，``0 or 默认值`` 会静默变回默认值，
把「设 0 表示不限制」变成永远做不到。
"""

import sys
import types

import numpy as np
import pytest

from imeeting.engine import pool, speaker
from imeeting.settings import config_store


@pytest.fixture
def set_cfg():
    """临时写一批配置覆盖，用例结束自动还原。"""
    saved = dict(config_store._db_overrides)

    def _set(**values):
        config_store.set_overrides(values)

    yield _set
    config_store._db_overrides = saved


class _AutoModelRecorder:
    """替身 AutoModel，只记录构造参数，不加载任何权重。"""

    last: "_AutoModelRecorder | None" = None

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        _AutoModelRecorder.last = self


@pytest.fixture
def fake_funasr(monkeypatch):
    """用假 funasr 模块替换真模块，避免测试里 import torch。

    两个 builder 都是函数内 ``from funasr import AutoModel``，
    所以替换 ``sys.modules['funasr']`` 即可生效。
    """
    mod = types.ModuleType("funasr")
    mod.AutoModel = _AutoModelRecorder
    _AutoModelRecorder.last = None
    monkeypatch.setitem(sys.modules, "funasr", mod)
    return _AutoModelRecorder


# ---------------------------------------------------------------------------
# 1) 建模参数必须进模型缓存指纹
# ---------------------------------------------------------------------------


class TestBuildKeyCoversModelConfig:
    """改了建模参数却命中旧缓存，就是「改了没反应」的根因。"""

    @pytest.mark.parametrize(
        "key,value",
        [
            ("asr.hub", "hf"),
            ("asr.model_revision", "v1.0.0"),
            ("asr.disable_update", False),
        ],
    )
    def test_streaming_key_changes(self, set_cfg, key, value):
        before = pool._build_key("streaming")
        set_cfg(**{key: value})
        assert pool._build_key("streaming") != before, f"{key} 改了但流式模型指纹没变"

    @pytest.mark.parametrize(
        "key,value",
        [
            ("asr.hub", "hf"),
            ("asr.model_revision", "v1.0.0"),
            ("asr.vad_model", "my-vad"),
            ("asr.punc_model", "my-punc"),
            ("asr.spk_model", "my-spk"),
            ("vad.max_segment_ms", 20000),
        ],
    )
    def test_offline_key_changes(self, set_cfg, key, value):
        before = pool._build_key("offline")
        set_cfg(**{key: value})
        assert pool._build_key("offline") != before, f"{key} 改了但离线模型指纹没变"


# ---------------------------------------------------------------------------
# 2) 建模时确实把配置传给了 AutoModel
# ---------------------------------------------------------------------------


class TestBuildersPassConfig:
    def test_streaming_passes_hub_and_revision(self, set_cfg, fake_funasr):
        set_cfg(**{"asr.hub": "hf", "asr.model_revision": "rev9", "asr.disable_update": False})
        pool._build_streaming_model()
        kw = fake_funasr.last.kwargs
        assert kw["hub"] == "hf"
        assert kw["model_revision"] == "rev9"
        assert kw["disable_update"] is False

    def test_offline_passes_submodel_names(self, set_cfg, fake_funasr):
        set_cfg(
            **{
                "asr.hub": "hf",
                "asr.model_revision": "rev9",
                "asr.vad_model": "my-vad",
                "asr.punc_model": "my-punc",
                "asr.spk_model": "my-spk",
                "vad.enabled": True,
                "punc.enabled": True,
                "spk.enabled": True,
            }
        )
        pool._build_offline_model()
        kw = fake_funasr.last.kwargs
        assert kw["vad_model"] == "my-vad"
        assert kw["punc_model"] == "my-punc"
        assert kw["spk_model"] == "my-spk"
        assert kw["hub"] == "hf"
        assert kw["model_revision"] == "rev9"

    def test_offline_skips_disabled_submodels(self, set_cfg, fake_funasr):
        """关掉 VAD / 标点 / 说话人后不应再传对应模型名，否则白加载。"""
        set_cfg(**{"vad.enabled": False, "punc.enabled": False, "spk.enabled": False})
        pool._build_offline_model()
        kw = fake_funasr.last.kwargs
        assert "vad_model" not in kw
        assert "punc_model" not in kw
        assert "spk_model" not in kw


# ---------------------------------------------------------------------------
# 3) 声纹模型名与最短音频
# ---------------------------------------------------------------------------


class _FakeEmbeddingModel:
    def __init__(self):
        self.calls = 0

    def generate(self, input=None):  # noqa: A002 - 与 FunASR 接口一致
        self.calls += 1
        return [{"spk_embedding": np.ones(speaker.EMBED_DIM, dtype=np.float32)}]


class TestSpeakerEmbeddingConfig:
    def test_embedding_model_name_follows_config(self, set_cfg, fake_funasr, monkeypatch):
        monkeypatch.setattr(speaker, "_model", None)
        monkeypatch.setattr(speaker, "_model_name", None)
        set_cfg(**{"spk.embedding_model": "my/sv-model"})
        speaker._load_model()
        assert fake_funasr.last.kwargs["model"] == "my/sv-model"

    def test_embedding_model_reloads_when_name_changes(self, set_cfg, fake_funasr, monkeypatch):
        """换了声纹模型必须重建，否则「改了没反应」。"""
        monkeypatch.setattr(speaker, "_model", None)
        monkeypatch.setattr(speaker, "_model_name", None)
        set_cfg(**{"spk.embedding_model": "my/sv-a"})
        first = speaker._load_model()
        set_cfg(**{"spk.embedding_model": "my/sv-b"})
        second = speaker._load_model()
        assert first is not second
        assert fake_funasr.last.kwargs["model"] == "my/sv-b"

    def test_min_audio_gate(self, set_cfg, monkeypatch):
        """低于「抽取声纹的最短音频」不调用模型；调小后短音频也能过。"""
        m = _FakeEmbeddingModel()
        monkeypatch.setattr(speaker, "_load_model", lambda: m)
        set_cfg(**{"spk.min_audio_ms": 1000})

        assert speaker.extract_embedding(np.zeros(16000 - 1, dtype=np.float32)) is None
        assert m.calls == 0, "音频不足 1.0s 时不应调用模型"

        emb = speaker.extract_embedding(np.zeros(16000, dtype=np.float32))
        assert m.calls == 1
        assert emb is not None and emb.shape == (speaker.EMBED_DIM,)

        # 下限降到 0.2s 后，短音频应能通过
        set_cfg(**{"spk.min_audio_ms": 200})
        speaker.extract_embedding(np.zeros(3200, dtype=np.float32))
        assert m.calls == 2

    def test_min_audio_zero_means_no_limit(self, set_cfg, monkeypatch):
        """0 是合法值（不限制），不能被 `or` 悄悄换成默认值。"""
        m = _FakeEmbeddingModel()
        monkeypatch.setattr(speaker, "_load_model", lambda: m)
        set_cfg(**{"spk.min_audio_ms": 0})
        assert speaker.extract_embedding(np.zeros(160, dtype=np.float32)) is not None
        assert m.calls == 1


# ---------------------------------------------------------------------------
# 4) 会话读取新的说话人阈值
# ---------------------------------------------------------------------------


class TestSessionReadsSpeakerThresholds:
    def _make(self):
        from imeeting.engine.streaming import StreamSession

        async def _noop(_msg):  # pragma: no cover - 仅占位
            return None

        return StreamSession(meeting_id=1, emit=_noop)

    def test_spk_min_speech_follows_config(self, set_cfg):
        set_cfg(**{"spk.min_speech_ms": 2500})
        assert self._make()._spk_min_speech_ms == 2500.0

    def test_spk_min_speech_zero_means_no_limit(self, set_cfg):
        """设 0 = 不限制。`get_config(...) or 1200` 会让它变成 1200。"""
        set_cfg(**{"spk.min_speech_ms": 0})
        assert self._make()._spk_min_speech_ms == 0.0

    def test_vad_min_speech_zero_means_no_filter(self, set_cfg):
        set_cfg(**{"vad.min_speech_ms": 0})
        assert self._make()._min_speech_ms == 0.0
