# TJproxy -- 同济 AI 平台 API 桥接

通过本地 Python 服务 + Chrome 插件，将[同济大学 AI 平台](https://agent.tongji.edu.cn)的应用对话能力暴露为标准 HTTP API（原生 SSE 格式及 OpenAI `/v1/chat/completions` 兼容格式），方便在终端、脚本、Python 程序或其他工具链中调用。
![icon](icon.png)

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
| `websockets` | 服务端集成测试的 WebSocket 客户端 |
| `requests` | 测试脚本 / SDK 示例 |
| `pytest` / `pytest-asyncio` | 测试运行器 |

### 2. 加载 Chrome 插件

#### Release加载：
1. 打开 `chrome://extensions`
2. 开启右上角 **开发者模式**
3. 将下载的 `TJproxy-Bridge-v0.1.0.zip` 文件拖入扩展页面加载


#### 源码加载：
1. 打开 `chrome://extensions`
2. 开启右上角 **开发者模式**
3. 点击 **加载已解压的扩展程序**
4. 选择 `D:\repos\TJproxy\extension\` 目录

加载后你会在扩展列表看到 **"TJproxy Bridge"**。

### 3. 启动 Python 服务

```bash
python server/main.py
```

启动成功后会打印：

```
TJproxy server listening on http://localhost:8765
```

长推理模型默认允许上游连续空闲 300 秒；流式响应每 15 秒发送一次 SSE
心跳。可在启动前通过 `TJPROXY_IDLE_TIMEOUT` 和
`TJPROXY_SSE_HEARTBEAT_INTERVAL`（单位：秒）调整。

---

## 使用方法

### 打开目标应用页面

在 Chrome 中访问你在同济 AI 平台上创建的任意 **智能体应用** 的**对话页面**，URL 形如：

```
https://agent.tongji.edu.cn/application/<appId>/chat
```

Extension 会自动提取该页面的 `appId` 并建立到 Python 服务的 WebSocket 连接。此后你的 HTTP 请求会被路由到该应用。

桥接连接使用扩展自动生成并持久化的本地令牌，只接受 `chrome-extension://` 来源。由于上游本质上是单一浏览器对话页，同时到达的 HTTP 对话请求会在服务端排队执行。

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

## 交互式 Agent CLI

可选的 CLI 在现有非流式 `/v1/chat/completions` 接口外增加本地 JSON 工具循环，现有 `/chat`、OpenAI 兼容接口和 Chrome 插件协议保持不变，不新增 `/agent` 接口。

```powershell
python agent_cli.py --workspace D:\repos\example
```

CLI 启动时会检测配置地址上的 TJproxy 服务：

- 已存在兼容服务时直接复用，退出 CLI 不会停止该服务。
- 没有服务时自动启动 `server/main.py`，等待就绪，并只在退出时停止自己创建的子进程。
- 端口被不兼容程序占用时直接报错，不会替换或终止该程序。

交互命令：

- 每次输入一个任务；当前模型输出、工具执行和后续模型请求全部结束后，才会显示下一个 `agent>` 提示符。
- `/new`：清空当前对话上下文。
- `/exit`：退出 CLI。

配置默认读取仓库根目录的 `agent.toml`，也可以指定其他文件：

```powershell
python agent_cli.py --workspace D:\repos\example --config D:\config\agent.toml
```

`service.base_url` 仅接受带明确端口的本机 HTTP origin（`localhost`、`127.0.0.1` 或 `::1`），不接受远端地址、凭据、路径、查询参数或重定向。

默认最多执行 32 轮“完整模型输出 -> 一个工具 -> 工具结果”，程序硬上限为 64 轮。支持 `read`、`write`、`edit` 和受限 PowerShell 7 管道；工作区在 CLI 启动后不可由模型修改。

### Agent 安全边界

文件路径被限制在所选工作区内，PowerShell 仅接受结构化 JSON stage 和 TOML 白名单中的命令、子命令与参数规则。文件修改应通过 `write` 和 `edit` 完成，PowerShell 不支持重定向、复合语句、变量展开、脚本块、提权或交互程序。

这属于**应用级安全策略，不是操作系统沙箱**。允许的测试工具、构建工具、包脚本和工作区脚本本身可以执行代码，能力可能超出参数检查范围。请仅对可信工作区和可信脚本使用 CLI。

---

## 端口配置

默认端口为 `8765`。如需修改，编辑两个文件：

1. **`server/main.py`** 第 20 行：`PORT = 8765`
2. **`extension/offscreen/offscreen.js`** 第 1 行：`WS_URL = 'ws://localhost:8765'`

将两处改为相同新端口后，重新加载插件并重启服务。

## 运行测试

```bash
python -m pytest server agent_tests -q
cd extension
npm ci
npm test
```

Python 3.11 及以上均可运行服务；测试命令中的解释器可按本机环境替换，但需先安装 `server/requirements.txt`。

---

## 技术实现说明

- **服务端**（`server/main.py`）：纯 Python 标准库实现（`asyncio` + 内置模块），不依赖任何 Web 框架。自行处理 HTTP/1.1 请求路由、WebSocket 协议（握手、帧编解码）、SSE 格式化、OpenAI 兼容响应生成，并用单一异步锁串行处理对话请求。
- **插件端**（`extension/`）：Chrome Manifest V3 架构。
  - `content.js`：注入 `agent.tongji.edu.cn` 页面，提取 URL 中的 `appId` 并通知 background。
  - `background.js`（Service Worker）：管理 offscreen 文档生命周期，持久化 AppID 与桥接令牌，并提供 Cookie 读取代理。
  - `offscreen/offscreen.js`：维护到 Python 服务的 WebSocket 长连接，接收 chat 指令后调用同济 SSE API，将 token 逐条回传。带指数退避自动重连。
- **API 设计**：`POST /chat` 返回原始 SSE token 流；`POST /v1/chat/completions` 兼容 OpenAI Chat Completions API，支持 `stream` 参数控制流式/非流式。

---
## 免责声明

本项目为个人学习开发的非官方工具，旨在探索同济 AI 平台的 Web to API 接入可能性。请勿将其用于任何商业或生产环境。使用过程中请遵守同济大学的相关使用政策和法律法规。开发者不对因使用本项目而产生的任何直接或间接损失负责。

---
## 许可证
本项目采用 MIT 许可证，详见 LICENSE 文件。
