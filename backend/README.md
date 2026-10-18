# iMeeting · 后端服务

基于 FunASR 的实时会议转写与智能纪要后端。

## 快速开始

```bash
cd backend

# 1. 安装依赖（CPU 环境）
pip install torch torchaudio --index-url https://download.pytorch.org/whl/cpu
pip install -e ".[asr]"

# 2. 配置（可选，不改也能跑）
cp .env.example .env

# 3. 启动
python -m imeeting.main
```

服务默认监听 `http://127.0.0.1:8000`。

- API 文档：http://127.0.0.1:8000/docs
- 健康检查：http://127.0.0.1:8000/api/v1/health

## 默认账号

首次启动会自动创建管理员 `admin / admin123`，**请立即修改密码**。

## 环境变量

见 `.env.example`。核心几项：

| 变量 | 说明 | 默认 |
|---|---|---|
| `SECRET_KEY` | JWT 签名密钥，生产必须修改 | 内置开发值 |
| `DATABASE_URL` | 数据库连接串 | `sqlite:///./data/meeting.db` |
| `FUNASR_DEVICE` | 推理设备 | `cpu` |
| `LLM_API_KEY` | 纪要 LLM 密钥 | 空 |
| `SKIP_MODEL_WARMUP` | 设为 1 跳过启动预热 | `0` |

## 架构

```
imeeting/
├── main.py           FastAPI 入口 + 生命周期
├── settings.py       三级配置解析（环境变量 > 数据库 > 默认值）
├── models.py         SQLModel 数据模型
├── db.py             引擎、建表、配置持久化
├── session_registry.py 进程内活跃会话注册表
├── security.py       JWT / bcrypt / 配置加密
├── login_throttle.py 登录防爆破（指数退避 + 账号/IP 双维度）
├── schemas.py        请求响应 DTO
├── engine/
│   ├── pool.py       FunASR 模型池（单例复用 + 线程隔离）
│   ├── audio.py      WAV 落盘（增量写 + 表头回填 + 过期清理）
│   └── streaming.py  双路 ASR 会话状态机
├── llm/
│   ├── client.py     OpenAI 兼容客户端
│   └── minutes.py    map-reduce 纪要生成
├── routers/
│   ├── auth.py       登录注册（含限流闸门）
│   ├── config.py     配置读写
│   ├── meetings.py   会议 CRUD + 纪要 + 导出 + 音频回放
│   └── system.py     健康检查
├── ws/
│   └── stream.py     WebSocket 转写流
└── export/
    └── formats.py    SRT / VTT / MD / TXT / JSON
```

## 双路 ASR 设计

一条音频流走两条路径：

- **流式路径**：每 600ms 出一帧 `partial`，用于实时字幕，延迟低
- **离线路径**：VAD 判定一句结束后送高精度模型，出 `final`，覆盖草稿

两条路径用**同一个 `segment_id`** 关联，前端据此覆盖而非追加。

⚠️ 流式模型的 `cache` 必须按会话隔离，共用会导致上下文串扰。

## WebSocket 协议

端点 `/ws/stream`

**客户端 → 服务端**

| 类型 | 载荷 |
|---|---|
| 文本 | `{"type":"start","title":"..."}` |
| 二进制 | 16kHz 单声道 float32 PCM |
| 文本 | `{"type":"stop"}` |
| 文本 | `{"type":"ping"}` |

**服务端 → 客户端**

```jsonc
{"type":"ready","meeting_id":1,"session_id":"abc"}
{"type":"partial","segment_id":"s-1","text":"我们这次会议","start_ms":0,"end_ms":1800}
{"type":"final","segment_id":"s-1","text":"我们这次会议主要讨论三个议题。","speaker":"1","seq":0}
{"type":"closed","session_id":"abc"}
```

## 测试

```bash
pip install -e ".[dev]"
pytest
```

## 性能说明（CPU）

CPU 推理是瓶颈，实测参考：

- 模型首次加载 10~40 秒 → 服务启动时**后台预热**，避免首请求等待
- 流式推理单块 600ms 音频约需 100~300ms（视 CPU）
- 建议 `asr.ncpu` 不超过物理核数的一半，避免与 torch 内部线程争抢

如果延迟累积，依次尝试：放宽分块到 800ms → 换 SenseVoiceSmall → 关闭说话人分离。
