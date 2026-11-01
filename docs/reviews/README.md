# 2026-10-31 代码与需求核查证据

<!-- doc-governance: IM-EVD-002 -->
> 文档类型：证据；状态：阶段快照；日期：2026-10-31。
> 维护责任：待指派（iMeeting 维护者）。[台账](../README.md) · [规则](../governance/README.md)。

本目录保留独立仓库可使用的核查证据，完整跨项目报告在联合工作区的 FunASR 工程文档中维护。

- [最终反例记录](evidence/2026-10-31/gap-probes-observed.xml)：15个规范断言失败，1个合法证据对照通过。失败是已复现的业务缺口。
- [探针源码](../../backend/tests/audit_doc_code_gaps_20261031.py)：显式运行，使用测试夹具的临时数据库，不加载模型、不访问生产数据。
- [2026-10-29 生产复核](../production-review-2026-10-29.md)：属于上一期结果，不能覆盖本次新增人员/模板路径。

本次确认的缺口包括：跨用户人员删除/模板读取；未确认模板跨会议可见；模板启用无恢复；临时身份确认未转正；同名不同单位重复建人；模糊引文可引入原文没有的姓名；非法模型字段抛异常；手动抽取无限流；人物改名不更新已有转写；同源模板重放改变质心；初始化口令进入日志；新旧匹配不兼容；数据库可接收第六个活跃slot；带模板声纹删除触发外键错误。

静态调用链还发现自动人员介绍抽取未接入开场/定稿/定时流程，转写 relabel 未接入模板冲突确认。函数存在不等于业务流程已经验收。

在 `iMeeting/` 目录复现：

```powershell
$env:PYTHONUTF8='1'
.\.venv-production-review\Scripts\python.exe -m pytest backend/tests/audit_doc_code_gaps_20261031.py --runxfail -q --tb=line --show-capture=no --disable-warnings
```

原核查时该命令退出码1，15失败/1通过保存在上述 XML 中。修复后原断言已复制为[常规回归](../../backend/tests/test_doc_code_gap_regressions.py)，不带 xfail；原探针保留原样供历史追溯，因此不再把原命令的退出码1作为修复后预期。

优先处理对象授权和日志口令，再修身份、模板状态与删除/合并，之后接通自动抽取和确认交互。模板匹配默认开关继续关闭，待上下文输入贯通和固定语料校准后灰度开启。

后续修复见[修复报告](../reports/2026-10-31-gap-fixes.md)和[新证据](evidence/2026-10-31-fixes/README.md)；R6 结构优化与性能对照见[续作报告](../reports/2026-10-31-r6-optimization.md)和[R6 证据](evidence/2026-10-31-r6/README.md)；身份统一（声纹—人员永久关联）见[身份报告](../reports/2026-10-31-identity-unify.md)和[身份证据](evidence/2026-10-31-identity-unify/README.md)；模板治理见[主模板报告](../reports/2026-10-31-template-primary.md)、[合并报告](../reports/2026-11-01-template-merge.md)及各自证据目录。本页保留原审查发现，当前实现状态以修复报告中对应范围为准。

2026-11-01 全面审查见[全面审查报告](2026-11-01-comprehensive-project-review.md)与[验证证据](evidence/2026-11-01-comprehensive-review/README.md)：以工作副本为受审对象，复现两条 P0（配置密钥重启后变密文、生产 bootstrap 回传管理员口令）与阈值三方不一致导致的 2 项红灯，并列明代理 IP、metrics 鉴权、WAV 头、分享令牌有效期等后续项。
