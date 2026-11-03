"""三级配置解析：环境变量 > 数据库 > 代码默认值。

设计要点
--------
1. 代码默认值保证零配置即可运行。
2. 数据库配置由管理员在后台可视化修改,支持热更新。
3. 环境变量优先级最高,用于密钥与部署相关参数,不进数据库明文。

配置项通过 ``CONFIG_SCHEMA`` 声明元数据,前端后台据此自动渲染表单。
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from dotenv import load_dotenv

from . import config_events

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parents[2]
load_dotenv(BASE_DIR / ".env")


# ---------------------------------------------------------------------------
# 配置项元数据：后台表单由它驱动，新增配置只需在此登记
# ---------------------------------------------------------------------------

FieldType = Literal["string", "int", "float", "bool", "select", "text", "password", "list"]


@dataclass(frozen=True)
class ConfigField:
    """单个配置项的元数据。"""

    key: str
    label: str
    group: str
    type: FieldType = "string"
    default: Any = None
    help: str = ""
    options: list[str] | None = None
    secret: bool = False
    restart_required: bool = False
    min_value: float | None = None
    max_value: float | None = None


def _f(**kwargs) -> ConfigField:
    return ConfigField(**kwargs)


CONFIG_SCHEMA: list[ConfigField] = [
    # ---------------- 模型与推理 ----------------
    _f(
        key="asr.mode",
        label="识别模式",
        group="模型与推理",
        type="select",
        default="normal",
        options=["normal", "echo", "off"],
        help=(
            "normal=真实 ASR；echo=回声模式，不做识别而是回显占位文本，"
            "用于在模型未就绪时联调前后端链路；off=只采集不产生文字。"
        ),
    ),
    _f(
        key="asr.device",
        label="推理设备",
        group="模型与推理",
        type="select",
        default="cpu",
        options=["cpu", "cuda:0", "mps", "npu:0"],
        help="本机无 GPU 时请保持 cpu。改动后需重启服务。",
        restart_required=True,
    ),
    _f(
        key="asr.hub",
        label="模型来源",
        group="模型与推理",
        type="select",
        default="ms",
        options=["ms", "hf"],
        help="ms=ModelScope（国内可直连，推荐）；hf=HuggingFace。改动后需重启服务。",
        restart_required=True,
    ),
    _f(
        key="asr.streaming_model",
        label="流式 ASR 模型",
        group="模型与推理",
        default="iic/speech_paraformer_asr_nat-zh-cn-16k-common-vocab8404-online",
        help="实时字幕使用的流式模型。改动后需重启服务。",
        restart_required=True,
    ),
    _f(
        key="asr.offline_model",
        label="离线 ASR 模型",
        group="模型与推理",
        default="paraformer-zh",
        help="定稿使用的高精度模型。paraformer-zh 别名会解析为 SeACo-Paraformer。",
        restart_required=True,
    ),
    _f(
        key="asr.model_revision",
        label="模型版本",
        group="模型与推理",
        default="master",
        help="模型仓库的分支或标签，master 表示最新。改动后需重启服务。",
        restart_required=True,
    ),
    _f(
        key="asr.vad_model",
        label="VAD 模型",
        group="模型与推理",
        default="fsmn-vad",
        help="离线定稿用的语音活动检测模型。仅「启用 VAD」时加载。改动后需重启服务。",
        restart_required=True,
    ),
    _f(
        key="asr.punc_model",
        label="标点模型",
        group="模型与推理",
        default="ct-punc",
        help="标点恢复模型。仅「启用标点恢复」时加载。改动后需重启服务。",
        restart_required=True,
    ),
    _f(
        key="asr.spk_model",
        label="说话人分离模型",
        group="模型与推理",
        default="cam++",
        help="离线定稿用的说话人分离模型。仅「启用说话人分离」且开启 VAD 时加载。",
        restart_required=True,
    ),
    _f(
        key="asr.ncpu",
        label="CPU 线程数",
        group="模型与推理",
        type="int",
        default=4,
        min_value=1,
        max_value=64,
        help="同时作用于 torch 与 BLAS。核数很多时并非越大越好。",
    ),
    _f(
        key="asr.infer_threads",
        label="推理线程数",
        group="模型与推理",
        type="int",
        default=0,
        min_value=0,
        max_value=32,
        help=(
            "并行执行 ASR 推理的线程数。0=自动（取 CPU 核数-1，至少 1）。"
            "该值与「最大并发会话数」应大致相当：线程太少会让并发会话排队，"
            "太多则会与 torch 内部线程争抢反而变慢。修改后需重启生效。"
        ),
    ),
    _f(
        key="asr.batch_size_s",
        label="动态批时长 (秒)",
        group="模型与推理",
        type="int",
        default=300,
        min_value=1,
        max_value=3600,
        help="离线定稿时的动态批总时长。",
    ),
    _f(
        key="asr.trim_silence_enabled",
        label="定稿前裁剪首尾静音",
        group="识别与断句",
        type="bool",
        default=True,
        help=(
            "只对送离线 ASR/声纹的副本裁剪首尾静音，保留 250ms 余量；"
            "原始片段和 VAD 时间轴不变。#231 真实会议中片段内语音只占约 49%。"
        ),
    ),
    _f(
        key="asr.trim_margin_ms",
        label="静音裁剪保留余量 (ms)",
        group="识别与断句",
        type="int",
        default=250,
        min_value=100,
        max_value=1000,
        help="裁剪时在首个/末个有效语音帧前后保留的音频，避免切掉轻声辅音。",
    ),
    _f(
        key="asr.chunk_size",
        label="流式分块 (ms)",
        group="模型与推理",
        type="list",
        default=[0, 10, 5],
        help="[0,10,5] 约 600ms，[0,8,4] 约 480ms。越小延迟越低但精度略降。",
    ),
    _f(
        key="asr.encoder_chunk_look_back",
        label="编码器回看块数",
        group="模型与推理",
        type="int",
        default=4,
        min_value=0,
        max_value=16,
    ),
    _f(
        key="asr.decoder_chunk_look_back",
        label="解码器回看块数",
        group="模型与推理",
        type="int",
        default=1,
        min_value=0,
        max_value=16,
    ),
    _f(
        key="asr.disable_update",
        label="跳过模型更新检查",
        group="模型与推理",
        type="bool",
        default=True,
        help="启动时是否联网检查模型版本。离线部署请保持开启，避免启动卡在联网。",
    ),
    _f(
        key="asr.warmup_on_start",
        label="启动时预热模型",
        group="模型与推理",
        type="bool",
        default=True,
        help=(
            "后台线程提前加载模型，避免首个请求等几十秒。"
            "仅识别模式为 normal 时生效。改动后需重启服务。"
        ),
        restart_required=True,
    ),
    # ---------------- 识别与断句 ----------------
    _f(
        key="vad.enabled",
        label="启用 VAD",
        group="识别与断句",
        type="bool",
        default=True,
        help="语音活动检测，用于断句与静音切分。关闭后不加载 VAD 模型。",
    ),
    _f(
        key="vad.silence_ms",
        label="静音断句阈值 (ms)",
        group="识别与断句",
        type="int",
        default=800,
        min_value=200,
        max_value=5000,
        help="连续静音超过该时长则判定一句话结束，触发离线定稿。",
    ),
    _f(
        key="vad.min_speech_ms",
        label="最短有效语音 (ms)",
        group="识别与断句",
        type="int",
        default=300,
        min_value=0,
        max_value=3000,
        help=("一句话里有效语音（能量过阈值的帧）累计时长低于此值就丢弃，"
              "用来滤掉键盘声、碰撞声等瞬时噪声。设 0 表示不过滤。"),
    ),
    _f(
        key="vad.max_segment_ms",
        label="单段最长时长 (ms)",
        group="识别与断句",
        type="int",
        default=30000,
        min_value=5000,
        max_value=120000,
        help="超过该时长强制断开，避免一直不断句。",
    ),
    _f(
        key="vad.soft_segment_ms",
        label="软切分起点 (ms)",
        group="识别与断句",
        type="int",
        default=20000,
        min_value=0,
        max_value=120000,
        help=(
            "超过该时长后，只要出现一次短暂停顿就定稿，避免一路拖到硬上限。"
            "0 表示关闭软切分。真实长会建议 20000~25000。"
        ),
    ),
    _f(
        key="vad.short_silence_ms",
        label="软切分停顿阈值 (ms)",
        group="识别与断句",
        type="int",
        default=250,
        min_value=50,
        max_value=2000,
        help="超过软切分起点后，出现该长度的停顿即定稿。",
    ),
    _f(
        key="asr.reorder_timeout_ms",
        label="定稿乱序等待上限 (ms)",
        group="识别与断句",
        type="int",
        default=1500,
        min_value=0,
        max_value=10000,
        help=(
            "严格按 seq 发字幕时，一个慢片段会堵住后面。超过该等待时间后"
            "允许后续片段先发，前端按时间轴插入。0 表示永远严格有序。"
        ),
    ),
    _f(
        key="audio.denoise_enabled",
        label="启用服务端降噪",
        group="识别与断句",
        type="bool",
        default=False,
        help=(
            "对离线定稿音频做谱减降噪（含 80Hz 高通），适合风扇/空调等稳态噪声。"
            "默认关闭；开启会增加少量 CPU 开销。"
        ),
    ),
    _f(
        key="audio.denoise_strength",
        label="降噪强度",
        group="识别与断句",
        type="float",
        default=0.3,
        min_value=0.1,
        max_value=3.0,
        help=(
            "#229 真实会议（SNR 中位 6.9dB）离线 A/B：0.55 档相似度略降、"
            "高频能量降 20%~30%，会损伤辅音；0.3 档基本持平。"
            "建议远场低 SNR 会议从 0.2~0.3 起步，并用 denoise-eval 复测。"
        ),
    ),
    _f(
        key="audio.agc_enabled",
        label="启用服务端自动增益",
        group="识别与断句",
        type="bool",
        default=False,
        help=(
            "把远场/低电平语音提升到目标电平后再送 ASR；静音段不会放大。"
            "VAD 仍使用原始能量，不会被增益干扰。"
        ),
    ),
    _f(
        key="audio.agc_target_rms",
        label="AGC 目标电平",
        group="识别与断句",
        type="float",
        default=0.05,
        min_value=0.01,
        max_value=0.2,
        help="目标 RMS。现场声音偏小时可保持 0.05；离麦很近时可降到 0.03。",
    ),
    _f(
        key="adaptive.enabled",
        label="启用会议级自适应",
        group="识别与断句",
        type="bool",
        default=True,
        help=(
            "按当前会议的噪声底、语音电平、片段长度和延迟自动调整增益、"
            "VAD 阈值与软切分。参数只影响当前会议，并保存快照用于复盘。"
        ),
    ),
    _f(
        key="adaptive.update_seconds",
        label="自适应更新周期 (秒)",
        group="识别与断句",
        type="int",
        default=15,
        min_value=5,
        max_value=120,
    ),
    _f(
        key="adaptive.gain_max",
        label="自适应最大增益",
        group="识别与断句",
        type="float",
        default=4.0,
        min_value=1.0,
        max_value=12.0,
        help="远场轻声时逐步提升用于 VAD/ASR 的电平，不会放大纯静音。",
    ),
    _f(
        key="adaptive.target_rms",
        label="自适应目标语音电平",
        group="识别与断句",
        type="float",
        default=0.035,
        min_value=0.01,
        max_value=0.15,
    ),
    _f(
        key="adaptive.initial_soft_segment_ms",
        label="自适应当初软切分 (ms)",
        group="识别与断句",
        type="int",
        default=12000,
        min_value=3000,
        max_value=60000,
    ),
    _f(
        key="adaptive.initial_max_segment_ms",
        label="自适应当初最大片段 (ms)",
        group="识别与断句",
        type="int",
        default=20000,
        min_value=6000,
        max_value=60000,
    ),
    _f(
        key="adaptive.short_merge_ms",
        label="短片段等待合并 (ms)",
        group="识别与断句",
        type="int",
        default=1800,
        min_value=0,
        max_value=5000,
        help="短于该时长的片段会多等一段停顿，尽量与下一句合并。",
    ),
    _f(
        key="asr.fallback_min_chars",
        label="草稿兜底最少字符",
        group="识别与断句",
        type="int",
        default=4,
        min_value=1,
        max_value=30,
        help="离线 ASR 为空、准备使用流式草稿时，低于该字符数直接拒收。",
    ),
    _f(
        key="punc.enabled",
        label="启用标点恢复",
        group="识别与断句",
        type="bool",
        default=True,
        help="对定稿文本补全标点。SenseVoice/Fun-ASR-Nano 自带标点，无需开启。",
    ),
    _f(
        key="use_itn",
        label="逆文本归一化",
        group="识别与断句",
        type="bool",
        default=True,
        help="把「二零二五年」转成「2025 年」。",
    ),
    _f(
        key="hotwords",
        label="热词表",
        group="识别与断句",
        type="list",
        default=[],
        help="提升专有名词识别率，例如公司名、产品名。",
    ),
    # ---------------- 说话人识别 ----------------
    _f(
        key="spk.enabled",
        label="启用说话人分离",
        group="说话人识别",
        type="bool",
        default=True,
        help="需要 speaker embedding 聚类，CPU 下会增加耗时。",
    ),
    _f(
        key="spk.threshold",
        label="说话人聚类阈值",
        group="说话人识别",
        type="float",
        # 0.5 而非 0.6：真实录音实测同一个人的跨句相似度会低到 0.54~0.58
        # （不同句子、不同音量、麦克风差异都会拉低），0.6 会把同一个人
        # 拆成多个「说话人 N」。0.5 在「合并同一人」和「区分不同人」之间
        # 更平衡。阈值越大越倾向于合并。
        default=0.5,
        min_value=0.1,
        max_value=1.0,
        help="越大越倾向于合并为同一说话人。真实录音建议 0.45~0.55。",
    ),
    _f(
        key="spk.min_speech_ms",
        label="判定说话人的最短语音 (ms)",
        group="说话人识别",
        type="int",
        default=1200,
        min_value=0,
        max_value=10000,
        help=(
            "有效语音短于该值的句子不做说话人判定，显示为「未知」。"
            "「别。」这类一两个字的片段声纹向量不稳，会把同一个人拆成多个说话人。"
            "设 0 表示不限制。"
        ),
    ),
    _f(
        key="spk.enrolled_threshold",
        label="已录入声纹命中阈值",
        group="说话人识别",
        type="float",
        default=0.75,
        min_value=0.4,
        max_value=0.95,
        help="跨会议识别姓名使用更严格阈值，宁可不给姓名，也避免认错老师。",
    ),
    _f(
        key="spk.enrolled_ambiguous_threshold",
        label="疑似已录入声纹阈值",
        group="说话人识别",
        type="float",
        default=0.68,
        min_value=0.3,
        max_value=0.9,
        help="介于该值与命中阈值之间时显示“疑似姓名”，不直接确认身份。",
    ),
    _f(
        key="spk.arbitration_margin",
        label="已知身份优先余量",
        group="说话人识别",
        type="float",
        default=0.03,
        min_value=0.0,
        max_value=0.2,
        help=(
            "已录入声纹分数与匿名簇接近时优先保留已知身份。"
            "例如已知 0.64、匿名簇 0.60，差值在余量内，应进入“疑似姓名”"
            "而不是被匿名簇吸收。"
        ),
    ),
    _f(
        key="spk.context_fallback_enabled",
        label="短句上下文回填",
        group="说话人识别",
        type="bool",
        default=True,
        help="短碎片在最近上下文高度一致时沿用邻近说话人，并标记为上下文推断。",
    ),
    _f(
        key="spk.context_max_gap_ms",
        label="上下文回填最大间隔 (ms)",
        group="说话人识别",
        type="int",
        default=5000,
        min_value=500,
        max_value=20000,
    ),
    _f(
        key="spk.low_confidence_threshold",
        label="低置信声纹提示阈值",
        group="说话人识别",
        type="float",
        default=0.62,
        min_value=0.3,
        max_value=0.9,
        help="低于该相似度的已标注姓名会在界面提示“低置信”，点击姓名可人工确认。",
    ),
    _f(
        key="spk.short_confidence_threshold",
        label="短片段低置信阈值",
        group="说话人识别",
        type="float",
        default=0.70,
        min_value=0.5,
        max_value=0.95,
        help=(
            "短句的声纹向量不稳定，低于该阈值即提示低置信。"
            "真实会议中 2 秒短句的 0.62~0.65 往往不足以确认身份。"
        ),
    ),
    _f(
        key="spk.short_confidence_ms",
        label="短片段语音时长 (ms)",
        group="说话人识别",
        type="int",
        default=1500,
        min_value=200,
        max_value=5000,
        help="有效语音短于该值时使用更高的低置信阈值。",
    ),
    _f(
        key="spk.short_confidence_chars",
        label="短片段文本字数",
        group="说话人识别",
        type="int",
        default=10,
        min_value=1,
        max_value=50,
        help="文本短于该字数时使用更高的低置信阈值。",
    ),
    _f(
        key="spk.min_audio_ms",
        label="抽取声纹的最短音频 (ms)",
        group="说话人识别",
        type="int",
        default=1000,
        min_value=0,
        max_value=10000,
        help="短于此长度的音频不抽声纹向量（向量不稳，容易误匹配）。",
    ),
    _f(
        key="spk.short_match_ms",
        label="短句回填最短语音 (ms)",
        group="说话人识别",
        type="int",
        default=700,
        min_value=0,
        max_value=10000,
        help=(
            "有效语音低于「判定说话人的最短语音」时，仍尝试与已有说话人比对。"
            "低于该值直接放弃，避免用极短音频误配。设 0 表示不限制。"
        ),
    ),
    _f(
        key="spk.short_match_threshold",
        label="短句回填阈值",
        group="说话人识别",
        type="float",
        default=0.68,
        min_value=0.1,
        max_value=1.0,
        help=(
            "短句只做「匹配已有说话人」，不会新建说话人类。"
            "阈值高于常规聚类阈值，以减少误标；建议 0.65~0.75。"
        ),
    ),
    _f(
        key="spk.global_merge_enabled",
        label="会后全局合并说话人",
        group="说话人识别",
        type="bool",
        default=True,
        help=(
            "会议结束后把只有少量片段的误分裂聚类并入最相似的稳定说话人，"
            "再生成终稿纪要。不会合并相似度不足的不同人。"
        ),
    ),
    _f(
        key="spk.merge_min_segments",
        label="参与合并的最大片段数",
        group="说话人识别",
        type="int",
        default=3,
        min_value=1,
        max_value=20,
        help="片段数少于该值的临时聚类才会被考虑合并。",
    ),
    _f(
        key="spk.merge_threshold",
        label="全局合并相似度阈值",
        group="说话人识别",
        type="float",
        default=0.45,
        min_value=0.1,
        max_value=0.95,
        help="临时聚类合并阈值；并入已命名身份时至少要求 0.55。",
    ),
    _f(
        key="spk.snapshot_every_n",
        label="临时声纹快照间隔 (段)",
        group="说话人识别",
        type="int",
        default=5,
        min_value=1,
        max_value=100,
        help=(
            "匿名簇质心会随句子漂移；每隔 N 段把当前质心写回临时声纹，"
            "避免重连或会后复核使用开场第一段的旧向量。"
        ),
    ),
    _f(
        key="spk.post_meeting_reconcile_enabled",
        label="会后声纹全局复核",
        group="说话人识别",
        type="bool",
        default=True,
        help=(
            "终稿前用较长片段重建每场会议的临时簇，并和正式声纹重新比对；"
            "可纠正实时阶段被匿名簇吸收的已知说话人。"
        ),
    ),
    _f(
        key="spk.reconcile_max_samples",
        label="会后复核抽样段数",
        group="说话人识别",
        type="int",
        default=5,
        min_value=2,
        max_value=20,
        help="用多少个最长/最完整的片段重建会议内说话人向量。",
    ),
    _f(
        key="spk.reconcile_segment_threshold",
        label="会后逐段回溯阈值",
        group="说话人识别",
        type="float",
        default=0.62,
        min_value=0.4,
        max_value=0.9,
        help=(
            "整簇命中正式身份后，逐段回溯使用更宽松的阈值；"
            "同一人在不同句子/音量下的相似度会低于整簇平均。"
        ),
    ),
    _f(
        key="spk.embedding_model",
        label="声纹向量模型",
        group="说话人识别",
        default="iic/speech_campplus_sv_zh-cn_16k-common",
        help="声纹录入与识别比对所用的模型（输出 192 维向量）。改动后下次抽取生效。",
    ),
    # ---- 多场景声纹模板（REQ-002） ----
    _f(
        key="spk.template_max",
        label="每人声纹模板上限",
        group="说话人识别",
        type="int",
        default=5,
        min_value=1,
        max_value=5,
        help="每人最多保留几条场景化声纹模板；替换模板即改 slot，库层硬约束不会超。",
    ),
    _f(
        key="spk.template_merge_sim",
        label="同人模板合并阈值",
        group="说话人识别",
        type="float",
        default=0.92,
        min_value=0.5,
        max_value=1.0,
        help="同一人的两条模板相似度超过该值即视为重复场景，合并为质量更高的一条。",
    ),
    _f(
        key="spk.template_align_sim",
        label="模板同场景对齐阈值",
        group="说话人识别",
        type="float",
        default=0.85,
        min_value=0.5,
        max_value=1.0,
        help="会中新质心与已有模板达到该相似度时视为同一场景，滚动更新而非新增。",
    ),
    _f(
        key="spk.template_sample_max",
        label="每人取样上限",
        group="说话人识别",
        type="int",
        default=32,
        min_value=3,
        max_value=500,
        help=(
            "会后聚类时为每人最多抽取多少条片段算嵌入（按时长与信噪比排序取优）。"
            "长会议逐段解码很贵，设上限可把耗时压到分钟级。"
        ),
    ),
    _f(
        key="spk.template_snr_floor",
        label="入模信噪比下限",
        group="说话人识别",
        type="float",
        default=0.0,
        min_value=0.0,
        max_value=25.0,
        help=(
            "低于该信噪比的片段不参与模板聚类。默认 0 = 不做硬过滤：浏览器麦克风"
            "实录常在 6–9 dB，设成 10 会滤掉全部真实片段，导致模板建不起来。"
        ),
    ),
    _f(
        key="spk.template_rebuild_enabled",
        label="会后自动生成场景模板",
        group="说话人识别",
        type="bool",
        default=True,
        help=(
            "会议收尾（纪要生成之后）自动为每位说话人跑一次场景模板聚类。"
            "每段音频都要算一次嵌入，长会议会多花一两分钟 CPU 时间。"
        ),
    ),
    _f(
        key="spk.template_embedding_cache_enabled",
        label="复用取样声纹向量",
        group="说话人识别",
        type="bool",
        default=True,
        help=(
            "会后重建模板时按片段缓存声纹向量，重跑同一场会议不再逐段解码音频。"
            "音频文件或片段边界变化后缓存自动失效。"
        ),
    ),
    _f(
        key="spk.template_embedding_cache_days",
        label="取样缓存保留天数",
        group="说话人识别",
        type="int",
        default=30,
        min_value=0,
        max_value=365,
        help="超过该天数的取样缓存会在下次重建时清理；0 表示不按时间清理。",
    ),
    _f(
        key="spk.template_cluster_sim",
        label="会后聚类合并阈值",
        group="说话人识别",
        type="float",
        default=0.65,
        min_value=0.3,
        max_value=0.99,
        help=(
            "会后聚类时两条片段的相似度超过该值即并为一簇（即得一类场景）。"
            "调高会分出更多模板、调低会合并更多场景。"
        ),
    ),
    _f(
        key="spk.self_min_sim",
        label="与本人差异过大阈值",
        group="说话人识别",
        type="float",
        default=0.45,
        min_value=0.1,
        max_value=0.9,
        help="确认样本与自己已有模板的最大相似度低于该值时提示「差异较大」。",
    ),
    _f(
        key="spk.conflict_sim",
        label="与他人类似阈值",
        group="说话人识别",
        type="float",
        default=0.60,
        min_value=0.3,
        max_value=0.95,
        help="样本与其他人的模板相似度达到该值且明显高于本人时提示冲突。",
    ),
    _f(
        key="spk.conflict_margin",
        label="冲突领先余量",
        group="说话人识别",
        type="float",
        default=0.05,
        min_value=0.0,
        max_value=0.3,
        help="他人相似度需比本人高出该值才算「更像别人」，避免边界抖动。",
    ),
    _f(
        key="spk.template_match_enabled",
        label="启用模板匹配",
        group="说话人识别",
        type="bool",
        default=False,
        help=(
            "开启后实时识别走「场景模板 + 多维融合」路径；关闭时保持旧的单质心"
            "线性比对。建议先跑校准脚本确认阈值后再开。"
        ),
    ),
    _f(
        key="spk.auto_identity_min_segments",
        label="自动身份最少片段数",
        group="说话人识别",
        type="int",
        default=3,
        min_value=1,
        max_value=50,
        help=(
            "会后清理：本场自动生成的「待命名」声纹，片段数少于该值**且**累计语音不足"
            "下一项时会被停用（停用可恢复，不删除）。人工命名过的身份不受影响。"
        ),
    ),
    _f(
        key="spk.auto_identity_min_ms",
        label="自动身份最少语音（毫秒）",
        group="说话人识别",
        type="int",
        default=3000,
        min_value=300,
        max_value=60000,
        help="与上一项同时不满足才会停用；3000 表示「不到 3 秒且不到 3 段」的簇视为噪声。",
    ),
    _f(
        key="spk.stage1_min_templates",
        label="启用粗筛的库规模",
        group="说话人识别",
        type="int",
        default=50,
        min_value=1,
        max_value=100000,
        help="模板总数超过该值时才启用场景桶粗筛，避免小库反而变慢。",
    ),
    _f(
        key="spk.stage1_topk",
        label="粗筛候选数",
        group="说话人识别",
        type="int",
        default=200,
        min_value=10,
        max_value=5000,
    ),
    _f(
        key="spk.fuse_w_emb",
        label="融合权重：声纹",
        group="说话人识别",
        type="float",
        default=0.85,
        min_value=0.0,
        max_value=1.0,
    ),
    _f(
        key="spk.fuse_w_scene",
        label="融合权重：场景",
        group="说话人识别",
        type="float",
        default=0.05,
        min_value=0.0,
        max_value=1.0,
    ),
    _f(
        key="spk.fuse_w_dur",
        label="融合权重：时长",
        group="说话人识别",
        type="float",
        default=0.05,
        min_value=0.0,
        max_value=1.0,
    ),
    _f(
        key="spk.fuse_w_ctx",
        label="融合权重：上下文",
        group="说话人识别",
        type="float",
        default=0.05,
        min_value=0.0,
        max_value=1.0,
    ),
    # ---- 人员本体（REQ-001） ----
    _f(
        key="person.extract_enabled",
        label="启用参会人介绍抽取",
        group="人员本体",
        type="bool",
        default=True,
        help="会中检测到「我是/这位是/介绍一下」等介绍句式时自动抽取人员信息。",
    ),
    _f(
        key="person.extract_interval_minutes",
        label="参会人抽取兜底间隔 (分钟)",
        group="人员本体",
        type="int",
        default=5,
        min_value=0,
        max_value=60,
        help="除句式触发外，每隔多久扫一次新转写；0 表示只靠句式与手动触发。",
    ),
    _f(
        key="person.max_auto_extracts",
        label="每场自动抽取上限",
        group="人员本体",
        type="int",
        default=3,
        min_value=1,
        max_value=20,
        help="控制 LLM 成本：开场一次 + 增量若干次的总上限。",
    ),
    _f(
        key="person.confidence_floor",
        label="参会人候选最低置信度",
        group="人员本体",
        type="float",
        default=0.4,
        min_value=0.0,
        max_value=1.0,
        help="低于该值的候选不默认展示，仅在「查看全部候选」里可见。",
    ),
    # ---------------- LLM 纪要 ----------------
    _f(
        key="llm.enabled",
        label="启用 LLM 纪要",
        group="LLM 纪要",
        type="bool",
        default=False,
        help="关闭时仅输出转写全文，不生成纪要。",
    ),
    _f(
        key="llm.base_url",
        label="API 端点",
        group="LLM 纪要",
        # 默认指向 DeepSeek（官方 OpenAI 兼容端点）。任何 OpenAI 兼容端点
        # 都可替换，例如本地 vLLM 的 http://localhost:8000/v1。
        default="https://api.deepseek.com/v1",
        help="任何 OpenAI 兼容端点。默认 DeepSeek：https://api.deepseek.com/v1",
    ),
    _f(
        key="llm.api_key",
        label="API Key",
        group="LLM 纪要",
        type="password",
        default="",
        secret=True,
        help="加密存储。也可用环境变量 LLM_API_KEY 覆盖。",
    ),
    _f(
        key="llm.model",
        label="模型名",
        group="LLM 纪要",
        # DeepSeek 当前在售模型：deepseek-chat / deepseek-reasoner。
        # 本机实测 deepseek-chat 可用（纪要生成正常）。
        default="deepseek-chat",
        help="DeepSeek 可用 deepseek-chat、deepseek-reasoner。",
    ),
    _f(
        key="llm.temperature",
        label="温度",
        group="LLM 纪要",
        type="float",
        default=0.3,
        min_value=0.0,
        max_value=2.0,
    ),
    _f(
        key="llm.max_tokens",
        label="最大输出长度",
        group="LLM 纪要",
        type="int",
        default=4096,
        min_value=256,
        max_value=32768,
    ),
    _f(
        key="llm.timeout",
        label="超时 (秒)",
        group="LLM 纪要",
        type="int",
        default=120,
        min_value=10,
        max_value=600,
    ),
    _f(
        key="llm.retries",
        label="失败重试次数",
        group="LLM 纪要",
        type="int",
        default=2,
        min_value=0,
        max_value=5,
    ),
    _f(
        key="llm.summary_window_minutes",
        label="分段摘要窗口 (分钟)",
        group="LLM 纪要",
        type="int",
        default=10,
        min_value=2,
        max_value=60,
        help="长会议按此窗口分段摘要，最后归并，避免超出上下文。",
    ),
    _f(
        key="llm.minutes_prompt",
        label="纪要提示词",
        group="LLM 纪要",
        type="text",
        default=(
            "你是一位专业的会议记录员。请根据以下会议转写内容，生成结构化的会议纪要。\n\n"
            "要求：\n"
            "1. 提取讨论的议题，每个议题给出简要说明\n"
            "2. 提炼每个议题的结论或共识\n"
            "3. 列出明确的待办事项，注明责任人与截止时间（如原文提及）\n"
            "4. 保持客观，不要臆造原文没有的信息\n\n"
            "输出 JSON，结构如下：\n"
            '{"summary":"整体摘要","topics":[{"title":"议题","detail":"说明","conclusion":"结论"}],'
            '"todos":[{"task":"待办","owner":"责任人","due":"截止时间"}]}\n\n'
            "会议转写内容：\n---\n{transcript}\n---"
        ),
    ),
    _f(
        key="llm.progressive_enabled",
        label="启用会中渐进纪要",
        group="LLM 纪要",
        type="bool",
        default=True,
        help=(
            "会议进行中每隔一段时间自动生成阶段草稿，并实时显示在纪要面板。"
            "需要 LLM 已配置；会产生额外的 API 调用费用。"
        ),
    ),
    _f(
        key="llm.progressive_interval_minutes",
        label="渐进纪要间隔 (分钟)",
        group="LLM 纪要",
        type="int",
        default=3,
        min_value=1,
        max_value=60,
        help="每隔多久生成一版草稿。会议很长时可适当调大以控制成本。",
    ),
    _f(
        key="llm.progressive_min_chars",
        label="渐进纪要最少新增字数",
        group="LLM 纪要",
        type="int",
        default=300,
        min_value=50,
        max_value=5000,
        help="新增转写内容少于该字数时跳过本次生成，避免无意义调用。",
    ),
    _f(
        key="llm.final_min_chars",
        label="自动终稿最少转写字数",
        group="LLM 纪要",
        type="int",
        default=80,
        min_value=20,
        max_value=5000,
        help=(
            "会议结束后转写达到该字数才会自动生成终稿。默认 80，"
            "可覆盖一分钟左右的短会；设为较大值可减少短会调用成本。"
        ),
    ),
    _f(
        key="llm.draft_model",
        label="渐进草稿模型",
        group="LLM 纪要",
        default="",
        help="留空表示与终稿使用同一模型；可填更便宜/更快的模型名以降低会中成本。",
    ),
    _f(
        key="llm.auto_final_on_stop",
        label="会议结束自动生成终稿",
        group="LLM 纪要",
        type="bool",
        default=True,
        help="收到 stop 后后台自动生成终稿，避免用户忘记点击；已有终稿时跳过。",
    ),
    # ---------------- 会话与安全 ----------------
    _f(
        key="session.max_concurrent",
        label="最大并发会话数",
        group="会话与安全",
        type="int",
        default=4,
        min_value=1,
        max_value=64,
        help="CPU 环境下建议不超过物理核数的一半。",
    ),
    _f(
        key="session.writer_enabled",
        label="会话写入走单写者",
        group="会话与安全",
        type="bool",
        default=False,
        help=(
            "开启后，WebSocket 会话的数据库写入统一交给一个串行写者线程执行，"
            "避免同步写把事件循环拖慢（IM-PLN-004 C-3）。默认关闭；"
            "切换后只影响新建立的会话，已在进行中的会议不受影响。"
        ),
    ),
    _f(
        key="session.close_timeout_s",
        label="会话收尾超时 (秒)",
        group="会话与安全",
        type="int",
        default=120,
        min_value=10,
        max_value=900,
        help=(
            "等待最后几段离线推理完成的最长时间。高负载下单段推理可能超过 30 秒，"
            "过短会取消任务并丢失缓冲中的定稿。超时后任务继续在后台跑完并落库。"
        ),
    ),
    _f(
        key="meeting.metadata_capture_minutes",
        label="口语录入会议信息时长 (分钟)",
        group="会话与安全",
        type="int",
        default=3,
        min_value=0,
        max_value=30,
        help=(
            "开始会议后的前 N 分钟，允许通过说话介绍会议主题、参会人和议程；"
            "到点后自动提取并填充会议信息。0 表示关闭。"
        ),
    ),
    _f(
        key="meeting.metadata_min_chars",
        label="口语录入最少字数",
        group="会话与安全",
        type="int",
        default=30,
        min_value=0,
        max_value=2000,
        help="窗口内转写内容少于该字数时不做自动提取。",
    ),
    _f(
        key="job.heavy_workers",
        label="重任务并发数",
        group="后台任务",
        type="int",
        default=1,
        min_value=1,
        max_value=4,
        restart_required=True,
        help=(
            "终稿纪要、声纹复核和检索索引重建的并发数。每个任务都可能占用 "
            "CPU、模型或 LLM 配额，单机默认 1，避免拖慢实时转写。"
        ),
    ),
    _f(
        key="job.light_workers",
        label="轻任务并发数",
        group="后台任务",
        type="int",
        default=2,
        min_value=1,
        max_value=8,
        restart_required=True,
        help=(
            "导出、会议元信息提取等任务的并发数。与重任务分池后，长纪要任务"
            "不会阻塞用户下载。"
        ),
    ),
    _f(
        key="translate.enabled",
        label="启用实时双语字幕",
        group="LLM 纪要",
        type="bool",
        default=False,
        help="定稿后用 LLM 翻译成目标语言并随字幕下发；会产生额外调用。",
    ),
    _f(
        key="translate.target_lang",
        label="翻译目标语言",
        group="LLM 纪要",
        default="en",
        help="例如 en（英语）、ja（日语）、zh（中文）。",
    ),
    _f(
        key="llm.translate_model",
        label="字幕翻译模型",
        group="LLM 纪要",
        default="",
        help="留空表示使用默认模型；可指定更便宜/更快的模型控制实时翻译成本。",
    ),
    _f(
        key="llm.max_concurrent_requests",
        label="LLM 最大并发请求",
        group="LLM 纪要",
        type="int",
        default=4,
        min_value=1,
        max_value=64,
    ),
    _f(
        key="llm.requests_per_minute",
        label="LLM 每分钟请求上限",
        group="LLM 纪要",
        type="int",
        default=60,
        min_value=1,
        max_value=10000,
        help="低于供应商限额可避免长会议触发 429。",
    ),
    _f(
        key="llm.meeting_budget_usd",
        label="单场会议 AI 预算 (USD)",
        group="LLM 纪要",
        type="float",
        default=0.0,
        min_value=0.0,
        max_value=10000.0,
        help="0 表示不限制；达到预算后新的纪要/翻译调用会被拒绝。",
    ),
    _f(
        key="llm.monthly_budget_usd",
        label="月度 AI 预算 (USD)",
        group="LLM 纪要",
        type="float",
        default=0.0,
        min_value=0.0,
        max_value=1000000.0,
        help="0 表示不限制；按本地记录的实际 token 成本累计。",
    ),
    _f(
        key="llm.input_price_per_million",
        label="输入价格 / 百万 Token (USD)",
        group="LLM 纪要",
        type="float",
        default=0.0,
        min_value=0.0,
        max_value=100000.0,
    ),
    _f(
        key="llm.output_price_per_million",
        label="输出价格 / 百万 Token (USD)",
        group="LLM 纪要",
        type="float",
        default=0.0,
        min_value=0.0,
        max_value=100000.0,
    ),
    _f(
        key="review.auto_after_meeting",
        label="会后自动全音频复核",
        group="LLM 纪要",
        type="bool",
        default=False,
        help="会议结束后后台重识别人工可读转写，只生成修订建议，不直接覆盖。",
    ),
    _f(
        key="review.similarity_threshold",
        label="音频复核差异阈值",
        group="LLM 纪要",
        type="float",
        default=0.72,
        min_value=0.1,
        max_value=1.0,
        help="新旧文本相似度低于该值时进入人工复核队列。",
    ),
    _f(
        key="embedding.enabled",
        label="启用语义向量检索",
        group="知识检索",
        type="bool",
        default=False,
        help="需要 OpenAI 兼容的 /embeddings 接口；关闭时使用 SQLite FTS。",
    ),
    _f(
        key="embedding.base_url",
        label="Embedding API 地址",
        group="知识检索",
        default="",
    ),
    _f(
        key="embedding.api_key",
        label="Embedding API Key",
        group="知识检索",
        type="password",
        default="",
        secret=True,
        help="加密存储，也可用环境变量 EMBEDDING_API_KEY 覆盖。",
    ),
    _f(
        key="embedding.model",
        label="Embedding 模型",
        group="知识检索",
        default="text-embedding-3-small",
    ),
    _f(
        key="export.company_name",
        label="导出公司名称",
        group="会话与安全",
        default="",
        help="写入 Word/PDF 导出的页眉。",
    ),
    _f(
        key="export.footer_text",
        label="导出页脚",
        group="会话与安全",
        default="",
        help="写入 Word/PDF 导出的页脚，例如“机密 · 内部使用”。",
    ),
    _f(
        key="export.pdf_font_path",
        label="PDF 导出字体文件",
        group="会话与安全",
        default="",
        help=(
            "PDF 导出使用的 TTF/TTC 字体绝对路径；留空时自动探测系统 CJK 字体，"
            "探测不到则用 reportlab 内置 STSong-Light（不内嵌字形）。"
            "环境变量 IMEETING_PDF_FONT 优先级更高。改完立即生效。"
        ),
    ),
    _f(
        key="session.max_duration_minutes",
        label="单会话最长时长 (分钟)",
        group="会话与安全",
        type="int",
        default=180,
        min_value=5,
        max_value=1440,
    ),
    _f(
        key="storage.audio_retention_days",
        label="音频留存天数",
        group="会话与安全",
        type="int",
        default=7,
        min_value=0,
        max_value=365,
        help="0 表示不保存音频，仅保留转写文本。",
    ),
    _f(
        key="storage.meeting_retention_days",
        label="会议文本保留天数",
        group="会话与安全",
        type="int",
        default=0,
        min_value=0,
        max_value=3650,
        help="0 表示不自动清理；法律保留中的会议永不自动删除。",
    ),
    _f(
        key="storage.trash_retention_days",
        label="回收站保留天数",
        group="会话与安全",
        type="int",
        default=30,
        min_value=1,
        max_value=365,
    ),
    _f(
        key="privacy.auto_redact",
        label="终稿生成后自动脱敏",
        group="会话与安全",
        type="bool",
        default=False,
        help="自动识别邮箱、身份证、手机号、银行卡和 IP；音频不会自动抹除。",
    ),
    _f(
        key="export.formats",
        label="允许的导出格式",
        group="会话与安全",
        type="list",
        default=["srt", "vtt", "md", "json", "txt", "docx", "pdf"],
    ),
    _f(
        key="auth.allow_register",
        label="开放注册",
        group="会话与安全",
        type="bool",
        default=False,
        help=("默认关闭：生产环境应由管理员在后台建号，避免任何人都能注册进来。"
              "需要自助注册时再打开。"),
    ),
    _f(
        key="share.max_failures",
        label="分享口令失败次数",
        group="会话与安全",
        type="int",
        default=5,
        min_value=1,
        max_value=100,
        help="同一分享链接与来源 IP 在窗口内允许的失败次数。",
    ),
    _f(
        key="share.lock_seconds",
        label="分享口令封禁时长 (秒)",
        group="会话与安全",
        type="int",
        default=60,
        min_value=10,
        max_value=3600,
    ),
    _f(
        key="upload.max_ics_bytes",
        label="日历文件大小上限 (字节)",
        group="会话与安全",
        type="int",
        default=2 * 1024 * 1024,
        min_value=1024,
        max_value=50 * 1024 * 1024,
    ),
    _f(
        key="upload.max_template_bytes",
        label="纪要模板大小上限 (字节)",
        group="会话与安全",
        type="int",
        default=10 * 1024 * 1024,
        min_value=1024,
        max_value=100 * 1024 * 1024,
    ),
    _f(
        key="auth.min_password_length",
        label="密码最小长度",
        group="会话与安全",
        type="int",
        default=6,
        min_value=4,
        max_value=64,
    ),
    _f(
        key="auth.login_throttle_enabled",
        label="登录防爆破限流",
        group="会话与安全",
        type="bool",
        default=True,
        help=("同一账号或同一来源 IP 连续登录失败后，按指数退避临时拒绝，"
              "防止撞库。关闭后仅保留密码哈希本身的耗时防护。"),
    ),
    _f(
        key="auth.max_login_failures",
        label="登录失败容忍次数",
        group="会话与安全",
        type="int",
        default=5,
        min_value=1,
        max_value=100,
        help="连续失败达到该次数后开始退避，之后每多失败一次等待时间翻倍。",
    ),
    _f(
        key="auth.login_lockout_seconds",
        label="首次锁定时长 (秒)",
        group="会话与安全",
        type="int",
        default=30,
        min_value=1,
        max_value=3600,
        help="退避基准时长，指数增长后最长 15 分钟封顶。",
    ),
    _f(
        key="auth.login_throttle_by_ip",
        label="按来源 IP 一并限流",
        group="会话与安全",
        type="bool",
        default=True,
        help=("开启后除账号外还按来源 IP 计数，可挡住「换用户名轮着试」的扫描。"
              "若前置了反向代理且未正确传递真实 IP，可关闭此项。"),
    ),
]

SCHEMA_BY_KEY: dict[str, ConfigField] = {f.key: f for f in CONFIG_SCHEMA}


# ---------------------------------------------------------------------------
# 环境变量覆盖表：secret 类配置优先从环境变量读取
# ---------------------------------------------------------------------------

ENV_OVERRIDES: dict[str, str] = {
    "llm.api_key": "LLM_API_KEY",
    "embedding.api_key": "EMBEDDING_API_KEY",
    "llm.base_url": "LLM_BASE_URL",
    "llm.model": "LLM_MODEL",
    "asr.mode": "ASR_MODE",
    "asr.device": "FUNASR_DEVICE",
    "asr.ncpu": "FUNASR_NCPU",
    # 绿色包/离线部署：允许用环境变量注入本地模型目录与 hub 行为，
    # 不写数据库即可把模型指向包内 models/。
    "asr.hub": "FUNASR_HUB",
    "asr.model_revision": "FUNASR_MODEL_REVISION",
    "asr.streaming_model": "ASR_STREAMING_MODEL",
    "asr.offline_model": "ASR_OFFLINE_MODEL",
    "asr.vad_model": "ASR_VAD_MODEL",
    "asr.punc_model": "ASR_PUNC_MODEL",
    "asr.spk_model": "ASR_SPK_MODEL",
    "asr.disable_update": "ASR_DISABLE_UPDATE",
    "asr.warmup_on_start": "ASR_WARMUP_ON_START",
    "spk.embedding_model": "SPK_EMBEDDING_MODEL",
    "session.max_concurrent": "MAX_CONCURRENT_SESSIONS",
    "job.heavy_workers": "JOB_HEAVY_WORKERS",
    "job.light_workers": "JOB_LIGHT_WORKERS",
}

# 服务自身配置（不暴露给后台）
#
# 一律用 _env_or 读取：环境变量存在但为**空串**时回退默认值。
# 直接用 os.getenv(name, default) 会有坑——.env 里写 `DATABASE_URL=` 时
# 该键存在且值为 ""，os.getenv 返回 ""，把默认值顶掉，
# 最终 SQLAlchemy 收到空 URL 报 "Could not parse SQLAlchemy URL"。
# 而按惯例「注释掉某配置」与「留空某配置」都表示用默认值。
def _env_or(name: str, default: str) -> str:
    raw = os.getenv(name)
    return raw if raw else default


SERVER_CONFIG = {
    "app_name": "总师会议智能伙伴",
    "secret_key": _env_or("SECRET_KEY", ""),
    "config_encryption_key": _env_or("CONFIG_ENCRYPTION_KEY", ""),
    "database_url": _env_or("DATABASE_URL", f"sqlite:///{BASE_DIR / 'data' / 'meeting.db'}"),
    "access_token_expire_minutes": int(_env_or("ACCESS_TOKEN_EXPIRE_MINUTES", "1440")),
    "cors_origins": _env_or("CORS_ORIGINS", "http://localhost:5173").split(","),
    "data_dir": Path(_env_or("DATA_DIR", str(BASE_DIR / "data"))).expanduser().resolve(),
}


# ---------------------------------------------------------------------------
# 配置管理器
# ---------------------------------------------------------------------------


@dataclass
class ConfigStore:
    """内存配置缓存,叠加数据库覆盖层。

    读取顺序：环境变量 > 数据库覆盖 > 代码默认值。
    写入只针对数据库层,环境变量层始终只读。
    """

    _db_overrides: dict[str, Any] = field(default_factory=dict)

    def get(self, key: str, default: Any = None) -> Any:
        """按三级回退读取配置。"""
        # 1) 环境变量（最高优先级）
        env_name = ENV_OVERRIDES.get(key)
        if env_name:
            raw = os.getenv(env_name)
            if raw not in (None, ""):
                return _coerce(raw, SCHEMA_BY_KEY.get(key))

        # 2) 数据库覆盖
        if key in self._db_overrides:
            return self._db_overrides[key]

        # 3) 代码默认值
        meta = SCHEMA_BY_KEY.get(key)
        if meta is not None:
            return meta.default

        return default

    def get_group(self, group: str) -> dict[str, Any]:
        """读取某一分组的全部配置。"""
        return {f.key: self.get(f.key) for f in CONFIG_SCHEMA if f.group == group}

    def snapshot(self, *, redact: bool = True) -> dict[str, Any]:
        """导出全部配置。redact=True 时对敏感字段脱敏。"""
        out: dict[str, Any] = {}
        for f in CONFIG_SCHEMA:
            value = self.get(f.key)
            if redact and f.secret and value:
                out[f.key] = "********"
            else:
                out[f.key] = value
        return out

    def load_overrides(self, rows: dict[str, str]) -> None:
        """从数据库行加载覆盖值。

        密钥类字段在库里是 Fernet 密文（见 ``config.py`` 的写入路径），装载时
        必须解密，否则进程重启后 ``llm.api_key`` 会以密文形式被当成 Bearer 发出去：
        当次填完当场可用，重启后全线 401，且后台只显示 ``********``，极难排查。
        旧版本落库的明文（没有 ``gAAAAA`` 前缀）原样保留，不因升级丢密钥。
        """
        parsed: dict[str, Any] = {}
        for key, raw in rows.items():
            meta = SCHEMA_BY_KEY.get(key)
            if meta is None:
                logger.debug("忽略未知配置项 %s", key)
                continue
            try:
                value: Any = json.loads(raw)
            except (json.JSONDecodeError, TypeError):
                value = _coerce(raw, meta)
            if meta.secret:
                value = _decode_secret(key, value)
            parsed[key] = value
        self._db_overrides = parsed
        logger.info("已加载 %d 项数据库配置覆盖", len(parsed))

    def set_overrides(self, values: dict[str, Any]) -> None:
        """热更新覆盖值（由配置路由调用,持久化在调用方完成）。"""
        for key, value in values.items():
            meta = SCHEMA_BY_KEY.get(key)
            if meta is None:
                continue
            self._db_overrides[key] = _coerce(value, meta)
        # 限流模块会缓存配置；改了安全相关配置必须让它立刻重新读取，
        # 否则管理员「关闭限流」或「调整阈值」要等到进程重启才生效。
        _notify_config_changed(values.keys())

    @property
    def overrides(self) -> dict[str, Any]:
        return dict(self._db_overrides)


def _notify_config_changed(keys: Any) -> None:
    """Notify modules that cache effective configuration values."""
    try:
        config_events.notify_changed(keys)
    except Exception:  # pragma: no cover - 通知失败不应影响配置写回
        logger.debug("配置变更通知失败", exc_info=True)


def _coerce(raw: Any, meta: ConfigField | None) -> Any:
    """把字符串或原始值转换成配置项声明的类型。"""
    if meta is None:
        return raw
    try:
        if meta.type == "bool":
            if isinstance(raw, bool):
                return raw
            return str(raw).strip().lower() in ("1", "true", "yes", "on")
        if meta.type == "int":
            return int(raw)
        if meta.type == "float":
            return float(raw)
        if meta.type == "list":
            if isinstance(raw, list):
                return raw
            text = str(raw).strip()
            if not text:
                return []
            if text.startswith("["):
                return json.loads(text)
            return [item.strip() for item in text.split(",") if item.strip()]
        return str(raw)
    except (ValueError, TypeError, json.JSONDecodeError) as exc:
        logger.warning("配置项 %s 值 %r 转换失败,回退默认值: %s", meta.key, raw, exc)
        return meta.default


def _decode_secret(key: str, value: Any) -> Any:
    """解密密钥类配置；明文（旧库）与空值原样返回。

    解密失败时返回空串而不是密文：密文当密钥用会让供应商返回 401，
    空串至少让 ``configured`` 判定为「未配置」，前端会明确提示去填。
    """
    if not isinstance(value, str) or not value:
        return value
    from .security import decrypt_value, is_encrypted_value

    if not is_encrypted_value(value):
        return value
    plain = decrypt_value(value)
    if not plain:
        logger.error(
            "配置项 %s 的密文无法解密（SECRET_KEY / CONFIG_ENCRYPTION_KEY 可能已变更），"
            "请在后台重新录入该项；本次按未配置处理",
            key,
        )
    return plain


# 全局单例
config_store = ConfigStore()


def get_config(key: str, default: Any = None) -> Any:
    """便捷读取函数。"""
    return config_store.get(key, default)


def config_float(key: str) -> float:
    """按 float 读取一个**已注册**的配置项。

    引擎里原来写的是 ``float(get_config("spk.enrolled_threshold") or 0.68)``：
    ``or 0.68`` 是第二份默认值，改了 CONFIG_SCHEMA 的 default 后它不会跟着变，
    于是后台显示 0.75、配置项为空时实际却是 0.68。这里以 schema 为唯一事实来源，
    未知键直接抛 KeyError（拼写错误应当立刻报错，而不是静默用兜底值）。
    """
    meta = SCHEMA_BY_KEY.get(key)
    if meta is None:
        raise KeyError(f"未注册的配置项：{key}")
    value = get_config(key, meta.default)
    return float(meta.default if value is None else value)


def validate_update(values: dict[str, Any]) -> tuple[dict[str, Any], dict[str, str]]:
    """校验一批配置更新。

    Returns:
        (清洗后的值, 错误字典)
    """
    cleaned: dict[str, Any] = {}
    errors: dict[str, str] = {}

    for key, value in values.items():
        meta = SCHEMA_BY_KEY.get(key)
        if meta is None:
            errors[key] = "未知配置项"
            continue

        coerced = _coerce(value, meta)

        if meta.type in ("int", "float") and meta.min_value is not None:
            if coerced < meta.min_value:
                errors[key] = f"不能小于 {meta.min_value}"
                continue
        if meta.type in ("int", "float") and meta.max_value is not None:
            if coerced > meta.max_value:
                errors[key] = f"不能大于 {meta.max_value}"
                continue
        if meta.type == "select" and meta.options and coerced not in meta.options:
            errors[key] = f"只能是 {' / '.join(meta.options)}"
            continue

        cleaned[key] = coerced

    return cleaned, errors
