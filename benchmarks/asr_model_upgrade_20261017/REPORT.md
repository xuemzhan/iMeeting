# ASR 模型升级验证报告（CPU · 2026-10-17）

## 1. 结论先行

1. **工具链已经是最新版。** 本机 FunASR 为 `1.4.16`，与 PyPI / GitHub 最新发布一致（2026-09-18）。
2. **网上确有更新、更强的模型，但 CPU 可落地性差异很大。** 真正值得在本机验证的是
   `Fun-ASR-Nano-2512`（0.8B，2025-12）和 `SenseVoiceSmall`（234M，2024-07）。
   Qwen3-ASR-1.7B、GLM-ASR-Nano-1.5B、MOSS-Transcribe-Diarize、Whisper-large-v3-turbo
   在本机 4 线程、16 GB 内存的约束下不作为首选。
3. **朗读集上 Nano 领先明显，真实会议集上优势不显著。**
   在 23 条可核对真值的 AISHELL-1 语音上，Nano GGUF Q5 的 CER 为 **0.59%**，
   现有 SeACo 为 **1.19%**，SenseVoice 为 **1.78%**。
   但在 40 条 WenetSpeech 真实会议语音上，Nano Q5 为 **11.00%**，SeACo 为 **11.82%**；
   只差 **0.82 个百分点**，配对 bootstrap 的 95% CI 为 **[-1.48, +3.35] pp**，p=0.51，
   **不能认为 Nano 在真实会议上显著更准**。
   不过在 10/5 dB 白噪声下，Nano 退化最小（2.33%/5.81%），SeACo 为 4.07%/7.56%，
   Nano 在噪声场景更有潜力。
4. **速度格局也要按同线程数重算。** 4 线程下 SeACo 约 **5.6 倍实时**，
   SenseVoice 约 **8.4 倍**，Paraformer 约 **8.1 倍**，Nano Q5 约 **4.4 倍**。
   Nano 只比 SeACo 慢约 20–25%，不是此前用“Nano 8 线程 vs SeACo 4 线程”比较出来的 2 倍差距。
5. **当前建议：保留 SeACo 作为生产离线模型，先不要整体替换。**
   Nano Q5 可以作为“离线定稿旁路”做更大规模灰度，但必须补足 200 条以上的真实会议人工标注；
   Nano 的 GGUF 路径没有热词参数，也不包含 CAM++，且每次请求都要启动一个进程。
   SenseVoiceSmall / SenseVoice GGUF 不建议作为升级目标：速度更快，但真实会议 CER 没有改善。

## 2. 本机环境

| 项目 | 实测值 |
|---|---|
| CPU | Intel Core i5-1240P，12 核 / 16 线程 |
| 内存 | 15.7 GB |
| 系统 | Windows 11 10.0.26200 |
| Python | 3.14.3 |
| PyTorch | 2.14.0+cpu |
| FunASR | 1.4.16 |
| 线上 ASR 线程 | `FUNASR_NCPU=4` |
| 现有流式模型 | `iic/speech_paraformer_asr_nat-zh-cn-16k-common-vocab8404-online` |
| 现有离线模型 | `iic/speech_seaco_paraformer_large_asr_nat-zh-cn-16k-common-vocab8404-pytorch` |
| 现有辅助模型 | FSMN-VAD、CT-Punc、CAM++ |

## 3. 在线版本与候选模型

### 3.1 工具链

- PyPI `funasr` 最新版：**1.4.16**。
- GitHub 最新 release：**v1.4.16**，发布时间 2026-09-18。
- 仓库内 `setup.py` / `funasr/version.txt` 与本地安装版本一致，没有“库落后”的问题。

### 3.2 候选模型筛选

| 模型 | 规模 / 时间 | 与现有方案的关系 | 本机 CPU 判断 |
|---|---|---|---|
| SeACo-Paraformer-Large | 0.2B / 现有 | 中文离线基线，支持热词 | **保留基线** |
| SenseVoiceSmall | 234M / 2024-07 | 非自回归、多语种、情感/事件标签 | 可跑，但精度未超过基线 |
| Fun-ASR-Nano-2512 | 0.8B / 2025-12 | SenseVoice 编码器 + Qwen3-0.6B 解码器 | **CPU 可跑，点估计最高；会议集未达显著** |
| Qwen3-ASR-1.7B | 1.7B | 52 语种 LLM-ASR | 参数与自回归解码成本过高，未纳入本轮 |
| GLM-ASR-Nano-2512 | 1.5B | 17 语种 LLM-ASR | 同上，未纳入本轮 |
| MOSS-Transcribe-Diarize | 未纳入 | 离线转写 + 匿名说话人标签 | 体量与运行时依赖不适合本机首轮验证 |
| Whisper-large-v3-turbo | 0.8B | 通用多语种 | 中文公开基准明显弱于 FunASR 系列，CPU 更慢 |

来源：仓库 `docs/model_selection_zh.md`、`examples/industrial_data_pretraining/fun_asr_nano/README_zh.md`、
`examples/industrial_data_pretraining/sense_voice/README_zh.md`、`runtime/llama.cpp/BENCHMARKS.md`，
以及 PyPI / GitHub / Hugging Face / ModelScope 的在线元数据。

## 4. 测试设计

### 4.1 测试范围

只比较**离线识别核心**（一条音频一次 `generate`），不比较流式首字延迟。
原因是本项目的实时字幕仍由流式 Paraformer 负责，本次升级问题集中在
“离线定稿用哪个模型更准、CPU 是否扛得住”。

### 4.2 语料

| 语料 | 数量 | 时长 | 用途 |
|---|---:|---:|---|
| AISHELL-1 测试集抽样 | 24 条 | 2.2 分钟 | 可核对真值的干净朗读语音 |
| 拼接长语音 | 6 条 | 2.2 分钟 | 20–30 秒连续语音，验证长段处理 |
| WenetSpeech test_meeting | 40 条 | 4.8 分钟 | 真实会议，人工标注，主会议 CER |
| 项目自录会议切片 | 2 条 | 1.3 分钟 | 只做定性完整性观察，不作为 CER 真值 |

说明：

- AISHELL-1 从公开数据集 `AudioLLMs/aishell_1_zh_test` 分层抽样，随机种子 `20261017`。
- 其中 `aishell1_0003` 经复核发现**参考文本末尾“市场风险”在音频中不存在**，
  单独切尾识别结果为“嗯”。该条从主统计中剔除，原始结果仍保留。
- 拼接长语音由已核对的 AISHELL-1 短句按顺序拼接，参考文本为对应短句文本串联，
  用于验证 20–30 秒连续语音是否会被截断。
- WenetSpeech `test_meeting` 从 `lmms-lab-audio/WenetSpeech` 的
  `test_meeting` 分片分层抽取 40 条（短/中/长 = 15/15/10），文本为该数据集自带的人工标注。
- 真实会议切片来自本项目 `iMeeting/backend/data/audio/200.wav`，
  参考文本是**现有生产转写**，不是人工真值，因此只用于观察完整性和模型间一致性。
- 公开的 AISHELL-4 Hugging Face 版本存在时间戳与单声道音频不对齐的问题，
  已从 CER 统计中移除，避免把数据错误误判为模型错误。
- AISHELL-1 是公开基准，候选模型可能在其训练数据中见过同源语料，
  因此绝对 CER 偏乐观；本轮数字主要用于**同口径横向排序**，不能直接外推为线上 CER。

### 4.3 指标与口径

- **CER（字符错误率）**：`Σ 编辑距离 / Σ 参考字符数`，micro 平均。
- 文本归一化：去掉标点、空白、SenseVoice 标签；ASCII 数字映射为中文数字，
  抵消 ITN（例如“10月8日”与“一零月八日”）的格式差异。
- **RTF**：`识别耗时 / 音频时长`，越小越好；`RTFx = 1 / RTF`。
- 计时不含模型加载和音频解码，包含 VAD 与识别；每个模型先做一次预热。
- PyTorch 模型统一 `ncpu=4`。Nano GGUF 使用官方 `llama-funasr-cli`，
  其编码器目前硬编码 8 线程，LLM 走 llama.cpp 默认线程；这一点在解读速度时必须注意。
- 峰值内存为进程 RSS；PyTorch 数值含框架常驻内存。

## 5. 结果

### 5.1 主结果

| 模型 | 运行路径 | AISHELL-1 CER（23 条） | 20–30s 连续语音 CER | RTF（短 / 长） | 进程峰值内存* |
|---|---|---:|---:|---:|---:|
| SeACo-Paraformer-Large（现状） | PyTorch CPU，4 线程 | **1.19%** | 1.78% | **0.159 / 0.153** | 3555 MB |
| SenseVoiceSmall | PyTorch CPU，4 线程 | 1.78% | 2.37% | 0.129 / 0.137 | 3364 MB |
| Fun-ASR-Nano-2512（GGUF Q5） | llama.cpp CPU，编码器 8 线程 | **0.59%** | **0.30%** | 0.371 / 0.211 | **1224 MB** |

\* 内存为**单模型干净进程**的峰值 RSS，不含线上额外常驻的 CT-Punc（约 1.1 GB）与 CAM++（约 28 MB）。
早期在同一进程里顺序加载多个模型时，旧模型内存没有及时释放，会得到 4.5 GB 的偏高读数；
上表已用独立进程复测修正。

按 24 条全量计算（不剔除坏样本）时，CER 分别为 SeACo 2.25%、SenseVoice 2.82%、
Nano 1.69%，排序不变。

### 5.2 速度与容量

- **按线上 4 线程同口径**：SenseVoice 约 **8.4 倍实时**，Paraformer 约 **8.1 倍**，
  SeACo 约 **5.6 倍**，Nano Q5 约 **4.4 倍**。
- Nano 的 8 线程编码器在 4 核绑核下收益不大：5.9 秒短音频 2.09 秒（RTF 0.356），
  25.9 秒长音频 5.71 秒（RTF 0.220），仍约 **2.8 倍 / 4.5 倍实时**。
  完整线程扩展见 5.6。
- Nano 并发实测（20–26 秒片段）：

| 并发数 | 单条耗时 | 总墙钟 | 说明 |
|---:|---:|---:|---|
| 1 | 4.3–5.4 s | 4.3–5.4 s | 8 线程编码器 |
| 2 | 4.5–4.7 s | 4.8 s | 基本线性，仍有裕量 |
| 4 | 11.1–12.8 s | 13.1 s | 32 线程争抢 16 逻辑核，单条延迟约 2.5 倍 |

4 路并发时 Nano 仍能跟上“每 20 秒一批”的节奏，但余量不大。
如果会议并发数继续上升，需要把 GGUF 改成常驻服务，避免每次都启动进程和重复加载模型。

### 5.3 真实会议观察（非 CER）

在 `200.wav` 的两段真实会议音频上：

- 三个模型都能继续输出生产转写中没有的内容，说明**现有数据库里的定稿文本存在漏句/截断**，
  这不一定是模型本身的问题，也可能与流式断句、定稿合并或写库路径有关。
- Nano 与 SenseVoice 的输出高度一致，且比生产转写更完整；
  SeACo 也能补出部分内容，但在个别长句上更保守。
- 由于没有人工真值，这一节只能作为“上线前需要补做真人标注会议测试集”的证据，
  不能据此给出 CER 结论。

### 5.4 WenetSpeech test_meeting（40 条，人工标注）

为了补上“真实会议 + 人工真值”这一环，又从公开的
`lmms-lab-audio/WenetSpeech` `test_meeting` 中分层抽取了 40 条会议语音：
短（<5 s）15 条、中（5–10 s）15 条、长（≥10 s）10 条，合计 4.8 分钟。

| 模型 | CER | 去语气词 CER | RTF | 进程峰值内存 |
|---|---:|---:|---:|---:|
| SeACo-Paraformer-Large | 11.82% | 11.34% | 0.149 | 3555 MB |
| Paraformer-Large | 12.15% | 11.67% | 0.113 | 3216 MB |
| SenseVoiceSmall（PyTorch） | 12.15% | 11.50% | 0.122 | 3364 MB |
| SenseVoiceSmall（GGUF Q8） | 12.81% | 12.26% | 0.081 | 289 MB |
| Fun-ASR-Nano Q4_K_M | 11.66% | 10.92% | 0.332 | 1383 MB |
| **Fun-ASR-Nano Q5_K_M** | **11.00%** | **10.41%** | 0.333 | 1224 MB |
| Fun-ASR-Nano Q8_0 | 11.58% | 10.92% | 0.342 | 1411 MB |

和朗读集不同，真实会议上的差距缩小到 1 个百分点以内：
Nano Q5 比 SeACo 低 0.82 pp，但比 SenseVoice/Paraformer 低 1.15 pp。
所有模型都从朗读集的 1%–2% CER 上升到 11%–13% CER，说明**远场、口语、重叠和噪声**
才是当前场景的主要误差来源。

### 5.5 配对显著性与误差拆解

同一批 40 条语音上做配对 bootstrap（5000 次重采样）：

| 配对（A-B） | CER 差 | 95% CI | p | A 更好 / B 更好 / 平 |
|---|---:|---:|---:|---:|
| SeACo - Nano Q5 | +0.82 pp | [-1.48, +3.35] | 0.51 | 9 / 11 / 20 |
| SeACo - Paraformer | -0.33 pp | [-0.92, +0.17] | 0.30 | 4 / 2 / 34 |
| SeACo - SenseVoice | -0.33 pp | [-2.02, +1.41] | 0.72 | 13 / 9 / 18 |
| SenseVoice GGUF - Nano Q5 | +1.81 pp | [+0.08, +3.64] | 0.047 | 8 / 16 / 16 |
| Nano Q4 - Q5 | +0.66 pp | [+0.00, +1.53] | 0.061 | 1 / 5 / 34 |
| Nano Q5 - Q8 | -0.57 pp | [-1.48, +0.16] | 0.188 | 5 / 3 / 32 |

结论：

- **没有任何一组“换生产模型”的差异达到统计显著。**
  Nano Q5 相对 SeACo 的点估计更好，但 95% CI 跨 0，p=0.51；
  40 条样本不足以证明 0.8 pp 的差距是真实的。
- Nano Q5 相对 SenseVoice GGUF 的 1.81 pp 差异接近显著（p=0.047），
  但 SenseVoice GGUF 本身是量化后的部署变体，不能代表 SenseVoice 的最佳精度。
- SeACo 相对 Paraformer 的 0.33 pp 也不显著，且 40 条里 34 条打平；
  SeACo 的价值更多在热词/上下文能力，而不是这批通用会议语音上的平均 CER。

按当前配对差值的标准差（约 8.8 pp）粗算：80% 功效下，检测 2 pp 差异约需 150 条，
检测 1 pp 差异约需 600 条。40 条只能排除“几个百分点”的大差距，不能证明 1 pp 以内的优劣。

错误类型（按参考字符数归一化）：

| 模型 | 替换率 | 删除率 | 插入率 |
|---|---:|---:|---:|
| SeACo | 6.32% | 5.09% | 0.41% |
| Paraformer | 6.40% | 5.25% | 0.49% |
| SenseVoice | 6.40% | 5.42% | 0.33% |
| Nano Q5 | **5.34%** | 5.09% | 0.57% |

- **替换是最大头**，但很多替换是语气词/近音字：`呃→嗯`、`呢→的`、`那→这`、`前→钱`。
- **删除率约 5%**，在长句和远场片段更明显；Nano 的插入率略高（0.57%），说明它更倾向于“补字”。
- 分时长看，Nano Q5 在短句（<5 s）明显更好（5.63% vs SeACo 10.80%），
  但在中句（5–10 s）反而更差（14.13% vs 11.66%），长句（≥10 s）略好（10.55% vs 12.34%）。
  这更像“不同模型在不同声学条件下的偏差”，而不是稳定的全面领先。
- 典型失败样本：`建筑安全消防这一块儿抓的特别严` 在 SeACo/SenseVoice/Nano 上都出现明显漏字或替换；
  `阻拦贴广告的人员` 也被多个模型识别成完全不同的词。此类样本更像远场/噪声导致的声学歧义，
  单靠换模型无法稳定解决。

### 5.6 量化与线程扩展

Nano 的量化对比（同一 40 条会议集）：

| 版本 | CER | RTF | 峰值内存 | 结论 |
|---|---:|---:|---:|---|
| Q4_K_M | 11.66% | 0.332 | 1383 MB | 更小，但没有更快，精度略低 |
| **Q5_K_M** | **11.00%** | 0.333 | 1224 MB | 本轮推荐 |
| Q8_0 | 11.58% | 0.342 | 1411 MB | 没有带来精度收益 |

线程扩展（同一条 25.9 s 连续语音，RTFx 越高越好）：

| 模型 | 1 线程 | 2 线程 | 4 线程 | 8 线程 |
|---|---:|---:|---:|---:|
| SeACo | 2.3x | 3.9x | **5.6x** | 7.7x |
| SenseVoice | 2.7x | 4.9x | **8.4x** | 11.2x |
| Paraformer | 2.8x | 4.8x | **8.1x** | 10.4x |
| Nano Q5 | 2.6x | 2.7x | **4.4x** | 5.8x |

几个关键点：

- **4 线程是线上真实配置**：SeACo 5.6x、SenseVoice 8.4x、Paraformer 8.1x、Nano Q5 4.4x。
  Nano 只比 SeACo 慢约 20–25%，不是此前“8 线程 Nano vs 4 线程 SeACo”算出的 2 倍。
- SenseVoice/Paraformer 在 4 线程下仍有 8 倍实时，适合多路并发；
  Nano 的扩展性在 2 线程后明显变差，4 路以上需要更谨慎地做队列和背压。
- SenseVoice GGUF 的 0.081 RTF（约 12.3 倍实时）是在默认运行时下测的，
  还没有做 1/2/4 线程归一化；它的内存只有 289 MB，是边缘部署的有力选项，
  但真实会议 CER 12.81%，没有超过 SeACo。

### 5.7 噪声鲁棒性

取同一批 12 条 AISHELL-1 干净语音，分别加入白噪声到 10 dB 和 5 dB SNR，
参考文本不变，观察 CER 退化：

| 模型 | 干净 | 10 dB | 5 dB | 10 dB 退化 | 5 dB 退化 |
|---|---:|---:|---:|---:|---:|
| SeACo | 1.74% | 4.07% | 7.56% | +2.33 pp | +5.82 pp |
| SenseVoice | 1.16% | 5.23% | 9.88% | +4.07 pp | +8.72 pp |
| **Nano Q5** | **0.58%** | **2.33%** | **5.81%** | **+1.75 pp** | **+5.23 pp** |

这是本轮最能支持 Nano 的一项证据：**噪声越强，Nano 的相对优势越明显**。
在 5 dB SNR 下，Nano 比 SeACo 低 1.75 pp，比 SenseVoice 低 4.07 pp。
不过这是白噪声，不是会议室里的空调、键盘、多人重叠或混响；只能说明趋势，
不能替代真实噪声场景的标注测试。

## 6. 决策建议

### 6.1 推荐路线

**短期（1–2 周）：保持 SeACo 为生产离线模型，先不要整体切换。**

理由：

- 40 条真实会议集上 SeACo 11.82% vs Nano Q5 11.00%，差距不显著（p=0.51），
  没有足够证据支持为了精度整体切换；
- 它是当前唯一同时具备热词、VAD、标点、CAM++ 现成集成的路径；
- 4 线程下约 5.6 倍实时，比 Nano Q5 的 4.4 倍多约 25% 的吞吐余量。

如果业务的主要痛点是**低信噪比、远场或嘈杂会议室**，Nano 在 10/5 dB 噪声下退化最小，
这是最值得做灰度的一类场景；如果主要是安静会议室和术语/人名准确率，
SeACo 的热词与现成集成更有价值。

**中期（条件性灰度）：只有在 200 条以上真实会议人工标注显示稳定收益后，
再把 Fun-ASR-Nano Q5 作为“离线定稿旁路”接入。**

- 流式 partial 继续用 Paraformer；
- 每个 VAD 定稿片段并行调用 Nano GGUF 生成最终文本；
- CAM++ 仍按现有方式对同一片段提取声纹；
- 标点可直接使用 Nano 自带输出，减少 CT-Punc 的 1.1 GB 常驻内存；
- 量化版本选 **Q5_K_M**：Q4 没有更快且略差，Q8 没有精度收益；
- 灰度期间同时保存 SeACo 与 Nano 两路文本，按会议类型分层抽检后再决定是否放量。

**不建议：把 SenseVoiceSmall 作为升级目标。**

它在 40 条真实会议上 CER 12.15%（GGUF Q8 为 12.81%），没有超过 SeACo 的 11.82%；
PyTorch 路径内存约 3.36 GB，优势主要是速度（4 线程约 8.4 倍实时），不是精度。
如果目标只是“更快、更省内存”，SenseVoice GGUF（289 MB）值得单独评估，
但不应把它当作精度升级。

### 6.2 需要接受的代价

- Nano GGUF 当前 CLI **没有热词参数**，行业术语/人名优势会丢失；
- GGUF 运行时**不包含 CAM++**，必须保留现有声纹链路；
- 每次请求启动进程，冷启动约 1.4 秒；高并发需要常驻服务封装；
- Nano 是自回归解码，2 线程后扩展性明显变差，4 路以上需要队列和背压；
- 本轮真实会议只有 40 条，配对 bootstrap 仍不足以证明 1 pp 以内的差异；
- 模型默认使用 `master` revision，正式复测应固定 revision/commit 并写入报告。

## 7. 下一步验证清单

1. **人工标注 200 条以上真实会议片段**（覆盖远场、多人、重叠、口音、专业术语），
   作为最终 CER 金标准；按当前方差，检测 2 pp 约需 150 条、1 pp 约需 600 条，
   推荐至少 200 条，条件允许时做到 500 条以上。
2. 用同一批片段对比 SeACo 与 Nano 的**热词命中率**、**术语错误率**、**漏句率**。
3. 把 Nano GGUF 封装成常驻 sidecar，测 2/4 路并发下的 P95 定稿延迟与内存。
4. 检查现有生产转写漏句问题：对比 `streaming.py` 的断句、`_merge_delta()`、
   `_drain_finals()` 与写库路径，确认是模型漏识还是链路丢段。
5. 如果 Nano 灰度通过，再评估是否把 CT-Punc 从常驻链路移除，以及是否调整
   `asr.infer_threads` / `session.max_concurrent`。

## 8. 复现方式

### 8.1 单项快速验证

只跑一条音频、同时对比现状与 SenseVoice：

```powershell
python run_bench.py --models seaco,sensevoice --limit 1 --out-dir results/one
```

只跑一条真实会议切片：

```powershell
python run_bench.py --models seaco,sensevoice --vad --corpus meeting_pseudo --limit 1 --out-dir results/one
```

### 8.2 完整复现

```powershell
cd iMeeting/benchmarks/asr_model_upgrade_20261017

# 1. 生成公开语料清单（已生成可跳过）
python prepare_corpus.py
python add_synth_long.py
python add_meeting_pseudo.py
python add_wenetspeech_meeting.py
python add_noise_corpus.py

# 2. PyTorch 基线 / SenseVoice
python run_bench.py --models seaco,sensevoice --vad
python run_bench.py --models seaco,sensevoice --vad --corpus synth_long --out-dir results/synth_long
python run_bench.py --models seaco,sensevoice,paraformer --vad --corpus wenetspeech_meeting --out-dir results/wenetspeech_meeting
python run_bench.py --models seaco,sensevoice --vad --corpus noise_snr10 --out-dir results/noise_snr10
python run_bench.py --models seaco,sensevoice --vad --corpus noise_snr05 --out-dir results/noise_snr05

# 3. Fun-ASR-Nano GGUF
python run_gguf_bench.py
python run_gguf_bench.py --corpora meeting_pseudo --out results/nano_gguf_meeting.json
python run_gguf_bench.py --corpora wenetspeech_meeting --out results/nano_gguf_wenetspeech.json
python run_gguf_bench.py --llm tools\gguf\qwen3-0.6b-q4km.gguf --label "Nano Q4" --corpora wenetspeech_meeting --out results/nano_q4_wenetspeech.json
python run_gguf_bench.py --llm tools\gguf\qwen3-0.6b-q8_0.gguf --label "Nano Q8" --corpora wenetspeech_meeting --out results/nano_q8_wenetspeech.json
python run_gguf_bench.py --engine sensevoice --llm tools\gguf\sensevoice-small-q8.gguf --label "SenseVoice GGUF Q8" --corpora wenetspeech_meeting --out results/sensevoice_gguf_wenetspeech.json
python run_gguf_bench.py --corpora noise_snr10,noise_snr05 --out results/nano_noise.json

# 4. 显著性与容量
python compare_models.py
python thread_scaling.py

# 5. 汇总
python summarize.py
```

原始逐条结果在 `results/`，汇总表在 `results/decision_summary.md`。

## 9. 数据与代码位置

- 语料清单：`manifest.jsonl`
- 音频：`audio/`
- PyTorch 评测：`run_bench.py`
- GGUF 评测：`run_gguf_bench.py`
- 汇总：`summarize.py`
- 真实会议集：`add_wenetspeech_meeting.py`
- 噪声集：`add_noise_corpus.py`
- 配对显著性与误差拆解：`compare_models.py`
- 线程扩展：`thread_scaling.py`
- 内存复测：`measure_memory.py`
- 原始结果：`results/*.json`，重点看
  `results/wenetspeech_meeting/`、`results/meeting_comparison.md`、
  `results/thread_scaling.json`、`results/noise_snr10/`、`results/noise_snr05/`、
  `results/nano_noise.json`。

