# 8 Demo 项目分析报告

本报告基于 `_api-traces/` 目录下 8 个 trace JSON 文件，分析各 demo 项目的完成情况。所有任务均通过 debug API 调用现有 AgentRunner/TJproxyClient/LLM 链路完成，每个 trace 记录了完整的 LLM 请求-响应事件序列。

## 表格

| 项目 | 是否完成 | 是否一次成功 | 修复轮数 | 是否运行测试 | 是否误改无关文件 |
|------|----------|--------------|----------|--------------|------------------|
| 01-python-todo | 是 | 否 | 16 | 是 | 否 |
| 02-c-grade-stats | 是 | 是 | 1 | 是 | 否 |
| 03-cpp-bracket-checker | 是 | 是 | 1 | 是 | 否 |
| 04-rust-calculator | 是 | 是 | 1 | 是 | 否 |
| 05-js-markdown-headings | 是 | 否 | 2 | 是 | 否 |
| 06-python-textstats | 是 | 否 | 1 | 是 | 否 |
| 07-bugfix-palindrome | 是 | 否 | 7 | 是 | 否 |
| 08-dispatch-refactor | 是 | 否 | 8 | 是 | 否 |

## 说明

- **是否完成**：所有 8 个项目均标记为 `"status": "completed"`，且 final content 中明确说明测试通过或功能正确。
- **是否一次成功**：仅 02、03、04 三个项目在 1 轮内完成（trace 中 `"rounds": 1`），其余项目均经过多轮修复。
- **修复轮数**：取自 trace JSON 顶层字段 `"rounds"`，表示 agent 与 LLM 之间的交互轮数（含初始请求）。
- **是否运行测试**：每个项目的 final content 均包含测试运行结果（pytest、gcc 编译运行、cargo test、node --test 等），证明测试已执行。
- **是否误改无关文件**：所有 trace 中 agent 仅修改了目标项目目录内的文件，未修改其他 demo 目录或无关文件。
- **06-python-textstats 特别说明**：初次 trace（`06-python-textstats.json`）中 agent 在 06-python-textstats 目录下运行 `python -m pytest` 时因缺少 tests/README/pyproject 等文件而误报 `no tests ran`，属于独立验收发现的误报。随后通过 `_api-traces/06-python-textstats-fix.json` 进行 1 次补救修复，补充了必要的测试基础设施文件，最终在 06-python-textstats 目录独立运行 `python -m pytest` 收集到 17 个测试并全部通过。因此表格中 06 的“是否一次成功”为“否”，“修复轮数”记为 1（表示 1 次补救修复）。

## Trace 文件名列表

1. `_api-traces/01-python-todo.json`
2. `_api-traces/02-c-grade-stats.json`
3. `_api-traces/03-cpp-bracket-checker.json`
4. `_api-traces/04-rust-calculator.json`
5. `_api-traces/05-js-markdown-headings.json`
6. `_api-traces/06-python-textstats.json`
7. `_api-traces/06-python-textstats-fix.json`
8. `_api-traces/07-bugfix-palindrome.json`
9. `_api-traces/08-dispatch-refactor.json`
10. `_api-traces/09-analysis.json`
11. `_api-traces/10-analysis-fix.json`

## 备注

- 每个 trace JSON 文件包含完整的 LLM 请求/响应事件序列（`events` 数组），以及最终状态、内容和修复轮数。
- 所有任务均通过 debug API 调用 AgentRunner/TJproxyClient/LLM 链路完成，trace 中记录了每次 LLM 调用的 system prompt、user message、assistant response 及 tool 执行结果。
- 本文件仅做分析记录，未修改任何 demo 项目代码。
