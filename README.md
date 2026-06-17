# 基于同济 Agent 平台改造的 API 桥接与简易 Agent 工具

TJproxy 通过本地 Python 服务和 Chrome 扩展，将[同济大学 Agent 平台](https://agent.tongji.edu.cn)中的智能体应用转换为可供终端、脚本和开发工具调用的 HTTP API，并提供一个基于严格 JSON 工具协议的简易本地 Agent CLI。

![TJproxy](icon.png)

> 本项目是非官方学习工具，需要使用者拥有可正常访问同济 Agent 平台的账号，并在 Chrome 中保持登录。

## 核心能力

### API 桥接

- 原始 SSE 接口：`POST /chat`
- OpenAI Chat Completions 兼容接口：`POST /v1/chat/completions`
- 支持流式和非流式响应
- 支持 OpenAI Python SDK、`requests`、curl 等客户端
- 自动复用 Chrome 中的登录状态和目标应用 `appId`

### 简易 Agent CLI

- 通过 Prompt 工程要求模型输出严格 JSON，而不是依赖原生 Tool Calling
- 支持 `project_map`、`list_dir`、`search`、`read_range`、`context_pack`、`read`、`write`、`edit` 和受限 PowerShell 7 工具
- 工作区在启动时固定，模型不能修改访问根目录
- 模型输出、工具执行和结果回传严格串行
- 默认最多执行 32 轮，可通过 TOML 配置调整，程序硬上限为 64 轮
- 默认共享会话上下文，可使用 `/new` 清空
- 自动检测并复用 TJproxy 服务；没有服务时自动启动并负责清理

## 工作原理

```text
同济 Agent 页面
      |
Chrome 扩展读取登录态并调用平台 SSE API
      |
WebSocket
      |
TJproxy 本地服务 :8765
      |                         |
/chat、/v1/chat/completions     Agent CLI
                                |
                         JSON 工具循环
                                |
               project_map / list_dir / search
               read_range / context_pack / read
                    write / edit / pwsh
```

上游本质上是一个浏览器对话通道，因此服务端使用全局锁串行处理请求。同一时刻只会进行一轮上游对话，后一请求必须等待前一请求完整结束。

## 环境要求

- Python 3.11+
- PowerShell 7，仅 Agent CLI 的 `powershell` 工具需要
- Chrome 或 Chromium 浏览器
- 已登录同济大学 Agent 平台的账号
- 一个可正常对话的智能体应用

## 安装

### Release 压缩包

Release 中的 `TJproxy-v0.2.0-windows.zip` 已包含浏览器扩展、TJproxy 服务、Agent TUI、配置文件和 Windows 启动脚本。下载后解压到任意目录，先运行 `doctor.bat` 检查本机环境，再运行 `start.bat` 启动 TUI；首次启动会自动创建 `.venv` 并安装 `server/requirements.txt` 中的 Python 依赖。

Chrome 扩展可以直接加载完整包内的 `TJproxy-Bridge-v0.2.0.zip`，也可以加载解压目录中的 `extension` 文件夹。

### 1. 安装 Python 依赖

```powershell
git clone https://github.com/CNYJ256/TJproxy.git
cd TJproxy
python -m pip install -r server/requirements.txt
```

### 2. 加载 Chrome 扩展

源码加载方式：

1. 打开 `chrome://extensions`。
2. 开启右上角的“开发者模式”。
3. 点击“加载已解压的扩展程序”。
4. 选择仓库中的 `extension` 目录。

也可以在扩展页面直接加载 Release 提供的 ZIP 包。

### 3. 打开目标 Agent 应用

在 Chrome 中打开目标应用的对话页面：

```text
https://agent.tongji.edu.cn/application/<appId>/chat
```

扩展会提取当前页面的 `appId`，读取浏览器登录 Cookie，并连接本地 TJproxy 服务。切换到其他应用页面后，后续请求将使用新的应用。

## API 桥接使用

### 启动服务

```powershell
python server/main.py
```

启动成功后输出：

```text
TJproxy server listening on http://localhost:8765
```

扩展可能需要数秒重新连接。如果请求返回“无 Extension 连接”，请确认目标应用页面仍然打开，然后稍后重试。

### 原始 SSE 接口

```powershell
curl.exe -X POST http://localhost:8765/chat `
  -H "Content-Type: application/json" `
  -d '{"message":"你好，请介绍一下自己"}'
```

响应示例：

```text
data: 你好
data: ！
data: [DONE]
```

### OpenAI 兼容接口

非流式请求：

```powershell
curl.exe -X POST http://localhost:8765/v1/chat/completions `
  -H "Content-Type: application/json" `
  -d '{"model":"tongji-agent","messages":[{"role":"user","content":"你好"}],"stream":false}'
```

Python `requests` 示例：

```python
import requests

response = requests.post(
    "http://localhost:8765/v1/chat/completions",
    json={
        "model": "tongji-agent",
        "messages": [{"role": "user", "content": "用一句话介绍同济大学"}],
        "stream": False,
    },
    timeout=330,
)
response.raise_for_status()
print(response.json()["choices"][0]["message"]["content"])
```

OpenAI Python SDK 示例：

```python
from openai import OpenAI

client = OpenAI(
    api_key="sk-placeholder",
    base_url="http://localhost:8765/v1",
)

response = client.chat.completions.create(
    model="tongji-agent",
    messages=[{"role": "user", "content": "你好"}],
)
print(response.choices[0].message.content)
```

`model` 和 `api_key` 当前不参与服务端鉴权。服务仅监听本机地址，不应直接暴露到公网。

## 简易 Agent CLI 使用

确保 Chrome 扩展已加载并打开目标 Agent 页面，然后运行：

```powershell
python agent_cli.py --workspace D:\repos\example
```

默认会启动 Textual TUI。CLI 会先检测 `agent.toml` 中的 TJproxy 地址：

- 已有兼容服务时直接复用，CLI 退出后不会关闭该服务。
- 没有服务时自动启动 `server/main.py`，退出时只关闭自己创建的进程。
- 端口被其他程序占用时停止启动，不会替换或终止该程序。

如果需要旧的 stdin/stdout 交互方式，可以加 `--plain`：

```powershell
python agent_cli.py --workspace D:\repos\example --plain
```

如果需要对真实 AgentRunner/TJproxyClient/LLM 调用链做自动化调试，可以启动仅监听本机的调试 HTTP 入口：

```powershell
python agent_cli.py --workspace D:\repos\example --debug-port 9876
```

调试入口复用普通 CLI/TUI 的同一套模型请求、工具协议、权限策略和一次性审批链路。它不会模拟模型；`POST /run` 会实际调用 `POST /v1/chat/completions`，并返回 LLM 请求、原始响应、工具调用和工具结果 trace。

常用端点：

| 端点 | 用途 |
|---|---|
| `GET /health` | 检查调试入口是否启动 |
| `POST /run` | 运行一个任务，body 为 `{"task":"..."}` |
| `POST /approve` | 继续一次待审批工具调用，body 为 `{"approval_id":"..."}` |
| `POST /reset` | 清空当前 Agent 会话上下文 |

最小请求示例：

```powershell
$body = @{ task = "阅读 README.md，概括项目能力，不要修改文件。" } | ConvertTo-Json -Compress
Invoke-RestMethod `
  -Uri "http://127.0.0.1:9876/run" `
  -Method Post `
  -ContentType "application/json" `
  -Body $body
```

TUI 快捷键：

```text
Enter       在多行输入框中换行
F10         发送当前输入，推荐使用
Ctrl+S      发送当前输入
Ctrl+Enter  发送当前输入，终端支持时可用
F4/Ctrl+V   粘贴到输入框
F9          复制完整输出到剪贴板
Ctrl+C      中断当前任务
Ctrl+D      退出 TUI
Ctrl+P      调出上一条输入历史
Ctrl+N      调出下一条输入历史
```

TUI 交互命令：

```text
/help     显示命令帮助
/exit     退出 TUI
/clear    清空可见输出
/copy     复制完整输出到剪贴板
/status   显示工作区、模式、轮数和运行状态
/reset    清空当前会话上下文
/plan     进入 plan 模式
/default  回到默认模式
```

plan 模式下，模型可以继续使用本地探索工具读取项目，但写入和编辑只允许落在 `docs/plan/*plan.md`，并会阻止 PowerShell 和代码文件修改。默认模式允许按工具策略正常写代码。

示例任务：

```text
agent> 阅读 README.md，告诉我项目提供了哪些接口，不要修改文件。
```

每次任务会按以下流程执行：

```text
用户任务
  -> 模型完整输出一个 JSON 对象
  -> CLI 校验 JSON Schema
  -> 执行至多一个工具
  -> 将结构化结果回传模型
  -> 重复，直到模型返回 final
```

模型输出不满足协议时不会执行任何内容，而是将协议错误回传模型重新生成。

## Demo 任务验证

`docs/demos` 保存了一组通过调试 HTTP 入口真实调用现有 AgentRunner/TJproxyClient/LLM 链路生成的 demo。目录中仅保留源码、测试、项目配置、trace 和 `analysis.md`；编译产物、缓存和虚拟环境产物不纳入保留范围。

| Demo | 内容 | 验证命令 | 结果 |
|---|---|---|---|
| `01-python-todo` | Python JSON 待办 CLI，支持 add/list/done/delete | `python -m pytest` | 8 passed |
| `02-c-grade-stats` | C 学生成绩平均值、最大值、最小值统计 | `gcc main.c -std=c11 -Wall -Wextra -o main.exe` + stdin 样例 | 编译通过，样例通过 |
| `03-cpp-bracket-checker` | C++17 `()[]{}` 括号匹配检查器 | `g++ main.cpp -std=c++17 -Wall -Wextra -o bracket.exe` + stdin 样例 | 编译通过，样例通过 |
| `04-rust-calculator` | Rust add/sub/mul/div 计算器，除零返回 `None` | `cargo test` | 5 passed |
| `05-js-markdown-headings` | Node.js Markdown 标题提取器 | `npm test` | 8 passed |
| `06-python-textstats` | Python `textstats` 多文件包 | `python -m pytest` | 17 passed |
| `07-bugfix-palindrome` | 复现并修复 `reversed(s)` 迭代器 bug | `python -m pytest` | 6 passed |
| `08-dispatch-refactor` | 将 calculator if/elif 分发重构为 dispatch 字典 | `python -m pytest` | 6 passed |

详细分析见 `docs/demos/analysis.md`，完整真实调用 trace 位于 `docs/demos/_api-traces/`。

## Agent 工具与安全边界

### 文件工具

- `project_map`：生成轻量项目地图，包含文件路径、大小、语言、顶层函数/类和前 20 行
- `list_dir`：列出工作区内目录条目及基础元数据
- `search`：在工作区内按文本查询返回 `path:line | content`
- `read_range`：按 1-based 闭区间读取文本行，避免一次读取大文件
- `context_pack`：按路径和查询词返回相关行号片段，作为本地轻量上下文包
- `read`：读取工作区内 UTF-8 文本文件，返回带行号的内容
- `write`：在工作区内创建或覆盖文件
- `edit`：执行具有预期替换次数的精确文本替换

路径必须相对工作区，程序会拒绝绝对路径、`..` 穿越、Windows ADS、设备名、符号链接和目录联接逃逸。

Agent CLI 的主链路优先使用本地文件探索工具，而不是让模型上传源码文件。普通文本代码文件通过 `project_map`、`list_dir`、`search`、`read_range` 和 `context_pack` 注入上下文；二进制文件不会作为核心上下文处理。

### PowerShell 7 权限策略

Agent 只使用 PowerShell 7，不抽象 bash。命令通过结构化 pipeline 提交，先经过 `agent.policy.toml` 策略判定，再交给 PowerShellExecutor 编译执行。

需要给被测程序提供标准输入时，不要写 `"..." | .\main.exe` 这类原始 shell 管道语法；在 `powershell.arguments.stdin` 中提供输入文本，例如：

```json
{"type":"tool_call","tool":"powershell","arguments":{"pipeline":[{"command":"main.exe","args":[]}],"stdin":"3\n80 90 100\n"}}
```

默认 profile 是 `dev`：常见只读、搜索、测试和构建检查可以直接运行；破坏性文件操作、VCS 重写、依赖安装、网络下载、服务启动、敏感文件访问会触发一次性审批。

未列入策略的未知命令默认不会直接执行：若未命中破坏性特征，会触发一次性审批；`rm`、`del`、`rmdir`、`Remove-Item` 等破坏性文件命令默认直接拒绝。

`/plan` 切换到 plan profile：模型仍可探索项目，但写入只允许 `docs/plan/*plan.md`，并禁止 PowerShell。

如果未提供 `agent.policy.toml`，Agent 使用内置默认策略；旧 `agent.toml` 中的 `[powershell.commands]` 会继续作为兼容命令合并到策略层。

这些限制属于应用级策略，不是操作系统级沙箱。测试工具、构建工具、包脚本和工作区脚本本身仍然可以执行代码。只应对可信工作区和可信脚本使用 Agent CLI。

## 配置

Agent 默认读取仓库根目录的 `agent.toml`：

```powershell
python agent_cli.py --workspace D:\repos\example --config D:\config\agent.toml
```

主要配置项：

| 配置 | 默认值 | 用途 |
|---|---:|---|
| `agent.max_rounds` | `32` | 单次任务最大模型轮数 |
| `service.base_url` | `http://localhost:8765` | 本地 TJproxy 地址 |
| `service.request_timeout_seconds` | `330` | 单次模型请求超时 |
| `powershell.timeout_seconds` | `60` | 命令执行超时 |
| `limits.output_chars` | `20000` | 工具输出字符上限 |

`service.base_url` 只接受带明确端口的本机 HTTP origin：`localhost`、`127.0.0.1` 或 `::1`。

### 服务超时与心跳

```powershell
$env:TJPROXY_IDLE_TIMEOUT = "300"
$env:TJPROXY_SSE_HEARTBEAT_INTERVAL = "15"
python server/main.py
```

### 修改端口

服务端口可通过环境变量设置：

```powershell
$env:TJPROXY_PORT = "9000"
python server/main.py
```

同时需要修改：

- `extension/offscreen/offscreen.js` 中的 `WS_URL`
- Agent 使用的 `agent.toml` 中的 `service.base_url`

## 项目结构

```text
TJproxy/
|- server/          本地 HTTP、SSE 与 WebSocket 桥接服务
|- extension/       Chrome Manifest V3 扩展
|- tjproxy_agent/   Agent 协议、工具、沙箱策略和运行循环
|- agent_cli.py     交互式 Agent 入口
|- agent.toml       Agent 默认配置
|- agent.policy.toml Agent 权限策略示例
|- agent_tests/     Agent 单元与端到端测试
`- README.md
```

## 测试

Python 测试：

```powershell
python -m pytest server agent_tests -q
```

扩展测试：

```powershell
cd extension
npm ci
npm test
```

## 已知限制

- 依赖浏览器中有效的同济统一认证登录状态。
- 依赖目标 Agent 应用页面保持打开。
- 上游为单对话通道，不支持并行模型请求或并行工具执行。
- Agent 工具协议由 Prompt 工程驱动，模型可能产生无效 JSON；CLI 会拒绝执行并请求修正。
- 平台接口或页面结构变化后，扩展可能需要同步更新。
- 仅面向本地学习和开发场景，不建议作为生产服务部署。

## 免责声明

本项目为个人学习开发的非官方工具，与同济大学及其 Agent 平台官方无关联。使用者应遵守学校相关规定、平台使用条款和适用法律，不得用于未授权访问、商业服务或其他违规用途。开发者不对使用本项目产生的直接或间接损失承担责任。

## 许可证

本项目采用 [MIT License](LICENSE)。
