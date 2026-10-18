# iMeeting · 前端

React 19 + Vite + TypeScript + Tailwind v4。

## 快速开始

```bash
cd frontend
npm install
npm run dev
```

打开 http://localhost:5173。开发服务器已配置代理，`/api` 和 `/ws` 会转发到 `http://127.0.0.1:8000`。

**先启动后端**，否则界面能打开但无法转写。

## 构建

```bash
npm run build      # 产物在 dist/
npm run preview    # 本地预览产物
```

## 目录

```
src/
├── main.tsx                    入口 + 路由
├── api/
│   ├── client.ts               REST 客户端（鉴权注入、错误处理）
│   ├── ws.ts                   WebSocket 客户端（重连、backlog 补发）
│   └── types.ts                接口契约类型
├── audio/
│   ├── recorder.ts             麦克风采集
│   └── pcm-worklet.js          AudioWorklet：重采样 + 分块
├── store/
│   ├── session.ts              转写会话状态
│   └── auth.ts                 认证状态
├── components/
│   ├── TopBar.tsx              顶部栏
│   ├── ControlBar.tsx          底部控制栏（大按钮）
│   ├── TranscriptStream.tsx    字幕流（自动跟随）
│   ├── SegmentCard.tsx         单条字幕卡片
│   ├── MinutesPanel.tsx        纪要面板
│   └── Waveform.tsx            实时波形
├── pages/
│   ├── Meeting.tsx             会议主界面
│   ├── MeetingDetail.tsx       历史会议详情
│   ├── History.tsx             历史列表
│   ├── Login.tsx               登录
│   └── admin/
│       ├── Admin.tsx           后台配置
│       └── ConfigFieldInput.tsx 配置项控件
└── styles/index.css            设计令牌 + 基础样式
```

## 音频链路

```
getUserMedia (16kHz 单声道)
  → AudioWorklet（独立音频线程，线性插值重采样）
  → 累积 600ms = 9600 采样
  → 转移 ArrayBuffer 所有权到主线程
  → WebSocket 二进制帧
```

**为什么用 AudioWorklet**：它跑在独立的音频线程，不受主线程渲染阻塞。用 `ScriptProcessorNode` 在主线程忙碌时会丢帧。

**为什么传裸 Float32 PCM 而非 Opus**：CPU 环境下服务端解码 Opus 会抢占本已紧张的推理算力。600ms 一帧 38.4KB（约 64KB/s），带宽完全可接受。

## 字幕渲染

关键约束：**同一个 `segment_id` 的 `final` 必须覆盖 `partial`，不能追加**。

流式输出是渐进修正的（"我们这次" → "我们这次会议" → "我们这次会议主要"），
若每帧追加会产生大量重复文本。`session.ts` 里用 `Map<segmentId, index>` 做 O(1) 覆盖更新。

自动滚动策略：贴底时跟随最新；用户手动上滚则暂停跟随并显示「回到最新」浮标。
这样用户回看前文时不会被新字幕拽走。

## 主题

设计令牌全部以 CSS 变量定义在 `styles/index.css`，`:root` 是浅色，`.dark` 是深色。
切换主题只需给 `<html>` 加/去 `dark` 类。

## 浏览器要求

- Chrome / Edge 90+，Firefox 90+，Safari 15+
- 需要 `AudioWorklet`（决定能否分块采集）
- 麦克风需要安全上下文：`https` 或 `localhost`
