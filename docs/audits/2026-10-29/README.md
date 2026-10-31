# 依赖复核证据

<!-- doc-governance: IM-EVD-001 -->
> 文档类型：证据；状态：阶段快照；内容基准日期：2026-10-29；整理日期：2026-10-31。
> 依赖扫描及测试原始文件索引；扫描只代表当时漏洞库和依赖版本。
> 维护责任：待指派（iMeeting 文档维护者）。[台账](../../README.md) · [维护规则](../../governance/README.md)。

- `prepush-backend-tests.xml`：推送前重跑后端全量测试，568 项通过；包含第三方警告。
- `python-shared-environment-before.json`：从共享环境扫描中按 iMeeting 已安装运行时
  依赖闭包筛选；包含 certifi、cryptography、Pillow 的升级前公告。不是全机包扫描。
- `python-isolated-after.json`：按生产安全约束新建的独立 Python 环境扫描，包含开发/
  审计工具，跳过 editable 项目自身；不含 `[asr]` 模型推理依赖。
- `npm-after.json`：2026-10-29 官方 registry 复扫结果，已知漏洞计数为 0。

扫描结论只对应当天的漏洞库和实际解析版本，不保证未来没有新增公告。
