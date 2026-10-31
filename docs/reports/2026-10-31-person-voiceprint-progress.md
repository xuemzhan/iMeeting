# 人员本体与多场景声纹阶段记录

<!-- doc-governance: IM-RPT-002 -->
> 文档类型：报告；状态：阶段快照；内容基准日期：2026-10-31；整理日期：2026-10-31。
> 从需求索引拆出并规范代码路径的 P0 实现和实测记录；本轮仅复查引用文件，不重跑模型/数据库。
> 维护责任：待指派（iMeeting 文档维护者）。[台账](../README.md) · [维护规则](../governance/README.md)。

记录日期：2026-10-31。下列实现与实测内容迁自需求索引，保留原始数值与已知边界；本次文档整理未重新执行其中测试。代码路径相对 `iMeeting/`，并已补全可点击的文件引用。

关联需求：[REQ-001](../requirements/REQ-001-person-ontology.md)、[REQ-002](../requirements/REQ-002-voiceprint-multiscene.md)。

## 实现状态（2026-10-31，P0 后端 + 前端 UI）

| 模块 | 代码 | 测试 |
|---|---|---|
| 人员本体模型与迁移 | [backend/src/imeeting/models.py](../../backend/src/imeeting/models.py)、[backend/migrations/versions/20261031_02_person_ontology.py](../../backend/migrations/versions/20261031_02_person_ontology.py) | [backend/tests/test_person_ontology.py](../../backend/tests/test_person_ontology.py)（31 例） |
| 抽取与证据校验 | [backend/src/imeeting/person_extract.py](../../backend/src/imeeting/person_extract.py)、[backend/src/imeeting/persons.py](../../backend/src/imeeting/persons.py) | 同上（幻觉姓名/引文不符/幂等/别名/同名歧义/绑定/合并） |
| 人员接口 | [backend/src/imeeting/api/http/routes/persons.py](../../backend/src/imeeting/api/http/routes/persons.py) | 同上（participants / 新增 / extract / patch / bind / persons 检索） |
| 会中自动加入参会人 | `engine/speaker.py::save_discovered` → `persons.ensure_auto_participant` | 同上（幂等、占位标签按会议隔离、真实姓名跨会议复用） |
| 声纹模板与冲突 | [backend/src/imeeting/engine/voice_template.py](../../backend/src/imeeting/engine/voice_template.py)、[backend/src/imeeting/engine/voice_match.py](../../backend/src/imeeting/engine/voice_match.py)、[backend/migrations/versions/20261031_03_voiceprint_templates.py](../../backend/migrations/versions/20261031_03_voiceprint_templates.py) | [backend/tests/test_voice_templates.py](../../backend/tests/test_voice_templates.py)（33 例，含 4 例真实数据回归） |
| 声纹模板接口 | [backend/src/imeeting/api/http/routes/voiceprint_templates.py](../../backend/src/imeeting/api/http/routes/voiceprint_templates.py) | 同上（templates / confirm-sample / conflicts / decide / backfill / rebuild） |
| 会后自动聚类 | [backend/src/imeeting/application/jobs.py](../../backend/src/imeeting/application/jobs.py)（纪要生成之后执行，`spk.template_rebuild_enabled` 控制） | 同上 |
| 前端 API 层 | [frontend/src/api/types.ts](../../frontend/src/api/types.ts)、[frontend/src/api/client.ts](../../frontend/src/api/client.ts)（`personApi.addParticipant`、`voiceprintApi.confirmSample` 等） | `tsc -b` 通过 |
| 前端 UI | [frontend/src/components/ParticipantsPanel.tsx](../../frontend/src/components/ParticipantsPanel.tsx)（单行 chips + 手工/语音添加）、[frontend/src/components/VoiceprintConflictList.tsx](../../frontend/src/components/VoiceprintConflictList.tsx)（三选一）、[frontend/src/components/VoiceprintTemplateSummary.tsx](../../frontend/src/components/VoiceprintTemplateSummary.tsx)（模板槽位摘要）+ [frontend/src/lib/participants.ts](../../frontend/src/lib/participants.ts)、[frontend/src/lib/speech.ts](../../frontend/src/lib/speech.ts)、[frontend/src/hooks/useParticipants.ts](../../frontend/src/hooks/useParticipants.ts) | `vitest run` 86 例通过 |

已知边界（不算缺陷，是刻意的）：

- **实时识别仍走旧路径**：`spk.template_match_enabled` 默认 `false`，模板匹配代码已就绪但需先在真实数据上跑校准再打开（`voice_match.match()` 与 `speaker.match_enrolled` 已接好开关）。
- **会后模板聚类需要音频文件**：`voice_template.rebuild_meeting_templates()` 依赖 `meeting.audio_path`，音频已被保留策略清理的会议只能靠 `templates/backfill` 导入旧质心。
- **冲突三选一目前挂在声纹页**：转写里点「确认说话人」时直接弹冲突尚未接（`confirm-sample` 还不接受按 speaker label 反查 `voiceprint_id`），属 P1。
- **单次会议只会产生 1–2 条模板**：一场会里设备/距离/噪声基本恒定，多场景模板要靠跨会议累积才长到 3–5 条。

## 实测记录（2026-10-31）

在 `meeting.db` 的**副本**上跑（不重启服务、不动生产库）：

| 项 | 结果 |
|---|---|
| 旧质心回填 | 10 条 `voiceprint` → slot 0 模板，0.1 s |
| 会后聚类（会议 #7，153 段/1 人） | `created=0 updated=1 skipped=0`，32 段取样耗时 47 s（约 1.4 s/段，CPU） |
| 单人多模板上限 | 实测 1 条（同一场景合并），库层 `UNIQUE(voiceprint_id, slot)` 兜住 ≤5 |
| 模板匹配抽查（会议 #7 均匀 8 段） | 8/8 命中「丁效」，分数 0.770–0.855，无 `low_confidence` |

两个由实测数据逼出来的修正：

1. **删掉 `snr_db >= 10` 硬门槛**。真实浏览器录音的信噪比中位数只有 6–8 dB（会议 #1 的 644 段里仅 25 段到 10，会议 #7 的 152 段里 0 段），硬过滤会让模板永远建不起来——`rebuild` 当时返回 `skipped=1`。改为「按时长与信噪比排序取前 `spk.template_sample_max`（默认 32）条」，信噪比只做质量权重，`spk.template_snr_floor` 默认 0 = 不过滤。
2. **会后聚类接进收尾任务，且排在纪要之后**。原来只有 `POST /voiceprints/templates/rebuild` 手动入口，模板实际上不会自动长出来；放在纪要前会明显推迟纪要与待办（每段 1–3 秒 CPU）。

