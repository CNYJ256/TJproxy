# TJproxy -- 同济 AI 平台 API 桥接

通过本地 Python 服务 + Chrome 插件，将[同济大学 AI 平台](https://agent.tongji.edu.cn)的应用对话能力暴露为标准 HTTP API（原生 SSE 格式及 OpenAI `/v1/chat/completions` 兼容格式），方便在终端、脚本、Python 程序或其他工具链中调用。

---

## 架构

```
Browser                          localhost:8765                       Your Script
┌──────────────────┐    WS     ┌──────────────┐    HTTP SSE         ┌──────────┐
│ Chrome Extension │◄─────────►│ Python Server│◄────────────────────│ curl /   │
│ (offscreen)      │  token流   │ (main.py)   │  POST /chat         │ Python / │
│                  │           │              │  /v1/chat/completions│ SDK      │
│  ▲ 提取Cookie    │           └──────────────┘                     └──────────┘
│  │ 注入appId      │
┌─┴──────────────┐
│ agent.tongji.edu.cn │  ← 同济 AI 平台
│  (你的智能体应用)   │
└─────────────────┘
```

1. **Chrome Extension**：注入 `agent.tongji.edu.cn` 页面，提取 `appId` 和认证 Cookie；由 offscreen 页维护到 Python 服务的 WebSocket 长连接。
2. **Python Server**：监听 `localhost:8765`，接收 Extension 的 WS 连接，对外提供 HTTP 接口；将 HTTP 请求转发给 Extension，后者调用同济 API，返回流式 token。
3. **Your Script**：通过标准 HTTP POST 或 OpenAI SDK 与本地服务交互。

---

## 前置条件

- **Python 3.11+**（服务端使用 `asyncio`、内置 `hashlib` 等，无需高版本特性，但建议 3.11+）
- **Chrome 浏览器**（加载解压的扩展）
- **同济大学统一认证账号**（在 Chrome 中已登录 https://agent.tongji.edu.cn）

---

## 安装步骤

### 1. 安装 Python 依赖

```bash
cd D:\repos\TJproxy
pip install -r server/requirements.txt
```

依赖清单（见 `server/requirements.txt`）：

| 包 | 用途 |
|---|---|
| `websockets` | 备用 WebSocket 支持 |
| `requests` | 测试脚本 / SDK 示例 |
| `pytest` / `pytest-asyncio` | 测试运行器 |

### 2. 加载 Chrome 插件

1. 打开 `chrome://extensions`
2. 开启右上角 **开发者模式**
3. 点击 **加载已解压的扩展程序**
4. 选择 `D:\repos\TJproxy\extension\` 目录

加载后你会在扩展列表看到 **"TJproxy Bridge"**。

> 也可以直接使用根目录下的 `extension.zip`（解压后加载）。

### 3. 启动 Python 服务

```bash
python server/main.py
```

启动成功后会打印：

```
TJproxy server listening on http://localhost:8765
```

---

## 使用方法

### 打开目标应用页面

在 Chrome 中访问你在同济 AI 平台上创建的任意 **智能体应用** 的**对话页面**，URL 形如：

```
https://agent.tongji.edu.cn/application/<appId>/chat
```

Extension 会自动提取该页面的 `appId` 并建立到 Python 服务的 WebSocket 连接。此后你的 HTTP 请求会被路由到该应用。

---

### curl 示例

#### 原始 `/chat` 接口（SSE 流式）

```bash
curl -X POST http://localhost:8765/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "你好，介绍一下你自己"}'
```

响应为 SSE 流，每行为一个 token：

```
data: 你好
data: ！我
data: 是
data: 同济
...
data: [DONE]
```

#### OpenAI 兼容接口 `/v1/chat/completions`（流式）

```bash
curl -X POST http://localhost:8765/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "tongji-agent",
    "messages": [{"role": "user", "content": "你好"}],
    "stream": true
  }'
```

#### OpenAI 兼容接口（非流式）

```bash
curl -X POST http://localhost:8765/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "tongji-agent",
    "messages": [{"role": "user", "content": "你好"}],
    "stream": false
  }'
```

返回标准 OpenAI 格式 JSON：

```json
{
  "id": "chatcmpl-...",
  "object": "chat.completion",
  "created": 1718360000,
  "model": "tongji-agent",
  "choices": [{
    "index": 0,
    "message": {"role": "assistant", "content": "你好！有什么可以帮助你的？"},
    "finish_reason": "stop"
  }]
}
```

---

### Python requests 示例

#### 非流式调用 OpenAI 兼容接口

```python
import requests

resp = requests.post(
    "http://localhost:8765/v1/chat/completions",
    json={
        "model": "tongji-agent",
        "messages": [{"role": "user", "content": "用一句话介绍同济大学"}],
        "stream": False,
    },
)
data = resp.json()
print(data["choices"][0]["message"]["content"])
```

#### 流式调用 OpenAI 兼容接口

```python
import requests

resp = requests.post(
    "http://localhost:8765/v1/chat/completions",
    json={
        "model": "tongji-agent",
        "messages": [{"role": "user", "content": "列举三个上海的地标建筑"}],
        "stream": True,
    },
    stream=True,
)

for line in resp.iter_lines(decode_unicode=True):
    if not line:
        continue
    if line.startswith("data: "):
        data = line[6:]
        if data == "[DONE]":
            break
        chunk = json.loads(data)
        content = chunk["choices"][0]["delta"].get("content", "")
        if content:
            print(content, end="", flush=True)
```

---

### openai Python SDK 示例

```python
from openai import OpenAI

client = OpenAI(
    api_key="任意值",                          # 本服务不校验 key
    base_url="http://localhost:8765/v1",       # 指向本地服务
)

# 流式调用
stream = client.chat.completions.create(
    model="tongji-agent",
    messages=[{"role": "user", "content": "你好"}],
    stream=True,
)

for chunk in stream:
    content = chunk.choices[0].delta.content or ""
    print(content, end="", flush=True)
```

> 注意：`model` 字段可以是任意字符串，服务端不依赖该值。`api_key` 可以填任意值（如 `"sk-placeholder"`），服务端不做校验。

---

## 端口配置

默认端口为 `8765`。如需修改，编辑两个文件：

1. **`server/main.py`** 第 20 行：`PORT = 8765`
2. **`extension/offscreen/offscreen.js`** 第 1 行：`WS_URL = 'ws://localhost:8765'`

将两处改为相同新端口后，重新加载插件并重启服务。

---

## 故障排除

| 现象 | 可能原因 | 解决方法 |
|---|---|---|
| `[ERROR] 无 Extension 连接` | 插件未加载、未打开目标页面，或 offscreen 页未建立 WS | 打开 `chrome://extensions`，确认插件已启用；然后在 Chrome 中打开 `https://agent.tongji.edu.cn/application/<appId>/chat` 任意应用对话页，等待几秒 |
| `[ERROR] 未登录` | 同济统一认证 Cookie 过期或缺失 | 在 Chrome 中访问 `https://agent.tongji.edu.cn` 并重新登录 |
| `[ERROR] 未打开同济应用页面` | 当前页面不是应用对话页 | 导航到应用的 `/chat` 页面（URL 中包含 `/application/<appId>/chat`） |
| `[ERROR] Extension 响应超时` | 同济 API 响应过慢或网络问题 | 重试；检查 `agent.tongji.edu.cn` 是否可访问 |
| 服务启动报端口占用 | 已有其他进程占用 8765 端口 | 结束占用进程，或修改端口（见上节） |
| openai SDK 连接失败 | 服务未启动或端口不一致 | 确认 `python server/main.py` 正在运行，且 `base_url` 端口正确 |

---

## 技术实现说明

- **服务端**（`server/main.py`）：纯 Python 标准库实现（`asyncio` + 内置模块），不依赖任何 Web 框架。自行处理 HTTP/1.1 请求路由、WebSocket 协议（握手、帧编解码）、SSE 格式化、OpenAI 兼容响应生成。单文件，约 650 行。
- **插件端**（`extension/`）：Chrome Manifest V3 架构。
  - `content.js`：注入 `agent.tongji.edu.cn` 页面，提取 URL 中的 `appId` 并通知 background。
  - `background.js`（Service Worker）：管理 offscreen 文档生命周期，提供 Cookie 读取代理（MV3 下 Service Worker 无法直接访问 `document.cookie`）。
  - `offscreen/offscreen.js`：维护到 Python 服务的 WebSocket 长连接，接收 chat 指令后调用同济 SSE API，将 token 逐条回传。带指数退避自动重连。
- **API 设计**：`POST /chat` 返回原始 SSE token 流；`POST /v1/chat/completions` 兼容 OpenAI Chat Completions API，支持 `stream` 参数控制流式/非流式。

---

## 项目结构

```
TJproxy/
├── extension/               # Chrome 插件
│   ├── manifest.json        # 插件清单 (MV3)
│   ├── background.js        # Service Worker
│   ├── content.js           # 页面注入脚本
│   ├── offscreen/           # Offscreen 页
│   │   ├── offscreen.html
│   │   └── offscreen.js     # WS 客户端 + 同济 API 调用
│   └── test/                # 插件测试
├── server/
│   ├── main.py              # Python 服务入口（单文件）
│   ├── requirements.txt     # Python 依赖
│   └── test_main.py         # 服务端测试
├── extension.zip            # 打包好的插件
└── README.md
```
