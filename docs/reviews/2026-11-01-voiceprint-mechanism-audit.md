# 声纹识别机制审查（2026-11-01）

审查对象：`engine/voice_match.py`、`engine/voice_template.py`、`application/speaker_confirmation.py`、`persons.py`（身份绑定）、`application/identity_access.py`。
证据来源：`backend/data/meeting.db` 只读快照（本地 11-01 15:0x）+ 声纹模板来源表 + `audit_log`。

## 1. 结论

机制本身（确认才写模板、冲突三选一、模板槽位治理）方向对，但存在一条**可自证的漂移链**：一次确认同时「写入本场样本 + 给全局声纹改名 + 绑定全局 speaker_key」，且会后重建只按 `speaker_key` 认身份、没有任何相似度闸门。于是错配会被永久固化，并反向污染历史会议。

## 2. 实证链条（audit_log 逐条可查）

| 时间(UTC) | 动作 | 载荷 | 后果 |
|---|---|---|---|
| 10-31 00:45:01 | `relabel_speaker` meeting:1 | 说话人 1 → 郭睿，voiceprint 1 | vp#1 = 会议 1 主讲（郭睿），基准向量来自 M1 音频，00:44:44 建档 |
| 11-01 01:12:48.108 | `speaker_identity_decision` meeting:13 | confirm_self，voiceprint 1，template 1 | 把 M13 的样本写进 **vp#1**，template id=1 来源 = `meeting:13` |
| 11-01 01:12:48.308 | `relabel_speaker` meeting:13 | 郭睿 → 周YJ，151 段，voiceprint 1 | **全局声纹 vp#1 被就地改名成"周YJ"**，郭睿的证据被覆盖 |
| 11-01 01:56 / 02:10 / 04:05 | 会后重建 | template 3/7/8 → vp#1 | vp#1 的四条模板全部来自 M13/14/16，**没有一条来自 M1** |
| 11-01 06:21 | `speaker_identity_decision` meeting:17 | vp17 与 vp1 冲突（0.9008 vs 0.9413） | 同一人/两人已不可分辨，两条身份并存（vp1≈vp17 余弦 0.973） |

据此，`speaker_key`（会议内声学簇键）在跨会议复用后被视为同一身份，M1 的"郭睿"与 M13-17 的"周YJ"被合并成一个声纹，M1 的 626 段文本标签仍是"郭睿"，而身份实体已是"周YJ"。

## 3. 发现的缺陷

1. **跨会议无闸门取信**（严重）：`voice_template.rebuild_meeting_templates` 只用 `Voiceprint.speaker_key == speaker_key` + 可见性挑声纹，不看会议归属、不算相似度，`store_template` 直接把样本并入加权质心。一次错配 → 模板被替换 → 下次更匹配（自确认闭环）。
2. **确认即改名全局身份**（严重）：`speaker_confirmation.preflight_relabel` 的确认路径会重命名被确认的 voiceprint（`voiceprint: 1`），单场会议的纠正会改写所有历史会议的展示身份。
3. **仲裁 margin 只在最高档生效**（中）：`voice_match.match` 原实现只在 `≥ tau_high + 0.06` 时校验 `best - runner_up ≥ margin`；0.68–0.74 区间两个相近身份直接取最高分。
4. **报告分数与命中对象不一致**（轻）：重排到"每人最高分"后 `emb_score` 取的是全库 argmax，不是该人的分数。
5. **一 key 多人 / 一人多 key**：`meeting_participant` 唯一键是 `(meeting_id, person_id)`，一个人一场会议只能绑一个 key；同一 key 在 M1 绑郭睿、在 M13/M17 绑周YJ，且 M13 内 217 段文本分裂为 151「周YJ」+ 66「郭睿」。`bind_speaker` 改绑时把旧 key 的段落改写成"未确认说话人"，反复改标定会抹掉文本证据。
6. **噪声实体无门槛**：`person` 5/6 由会议 16 的 1.8 秒单句（"不是。"/"我我后天九点。"）自动建档；`spk.min_speech_ms` 只用于取样，`spk.merge_min_segments` 只用于合并阶段。
7. **确认写入 confidence=1.0**：`bind_speaker` 恒写 1.0，冲突评估可被 `payload.decision` 直接绕过，误确认会被当作最强证据。
8. **检测到却不收敛**：`spk.template_merge_sim=0.92` 早已被 vp1≈vp17（0.973）越过，但合并只出建议、不闭环。

## 4. 已实施的优化

| 改动 | 位置 | 效果 |
|---|---|---|
| 跨会议取样闸门 `_sample_belongs_to` | `voice_template.py` | 声纹归属其他会议时，取样质心与它已有模板/基准向量须 ≥ `spk.rebuild_guard_sim`（默认 0.60），否则拒绝写入并计入新指标 `skipped_mismatch`，同时 warning 日志 |
| 本会议声纹优先 | `voice_template.py` | 先精确命中 `(meeting_id, speaker_key)`，避免别的会议的同名 key 抢走本场样本 |
| 仲裁 margin 全档生效 | `voice_match.py` | 0.68–0.74 区间领先不足时降级为「疑似 …」交人工，不再直接定人 |
| `emb_score` 归位 | `voice_match.py` | 报告给用户的相似度与被选中的声纹一致 |

回归：`test_voice_templates`、`test_review_followup_fixes`、`test_template_rebuild_optimization`、`test_voiceprints_api`、`test_identity_workflows`、`test_voiceprint_person_link`、`test_voiceprint_template_pin`、`test_template_merge`、`test_voice_optimization`、`test_speaker_importance` 共 **172 项通过**；`ruff check` 通过。

## 5. 建议的后续（未实施，需产品决策）

1. **确认改名要隔离身份**：改名是"这个人叫错了"还是"这是另一个人"必须显式区分；默认新建身份，不要就地重命名带 confirmed 模板的全局声纹。
2. **一次确认只做一件事**：写模板 / 改显示名 / 绑人员拆成三个动作，避免一次点击同时改动身份与证据。
3. **允许一人多 key**：放宽 `(meeting_id, person_id)` 唯一约束，改为 `(meeting_id, person_id, speaker_key)`，并把同人多簇标记为 `alt`，避免改绑时把旧段抹成"未确认说话人"。
4. **最小证据门槛建档**：`person`/`voiceprint` 自动创建要求 ≥3 段且累计语音 ≥3 秒；单句噪声不得建身份。
5. **已污染数据的修复**：`vp#1` 需回滚为"郭睿"并用 M1 音频重建模板（`POST /voiceprints/rebuild-identity` 走 M1 会新建同名身份，需先处理重名）；`vp#1` 与 `vp#17` 需合并或停用其一。
