# TJproxy Agent TUI Demo：C 程序编写、编译与样例测试

> 本示例展示 TJproxy Agent TUI 在一个本地编程任务中的完整交互过程：用户提出需求，Agent 写入源代码、调用本地工具编译并运行测试，最后返回总结结果。

---

## 会话概览

<div class="demo-meta">

| 项目      | 内容                                |
| ------- | --------------------------------- |
| Demo 类型 | 本地 Agent 编程任务                     |
| 目标语言    | C                                 |
| 任务内容    | 从 stdin 读取学生成绩并输出平均分、最高分、最低分      |
| 涉及工具    | `write`、`powershell`              |
| 输出文件    | `grade_stats.c`、`grade_stats.exe` |
| 测试样例    | 3 组                               |

</div>

---

## Transcript

### 1. 系统提示

<div class="demo-block system">

**系统**

```text
TJproxy Agent TUI
使用 /help 查看命令。Enter 换行，F10 或 Ctrl+S 提交多行输入。
```

</div>

---

### 2. 用户输入

<div class="demo-block user">

**用户**

```text
用 C 写一个程序，从 stdin 读取学生人数 n，然后读取 n 个整数成绩，输出平均分、最高分、最低分。要求处理 n <= 0 的情况。编译并用至少 3 组样例测试。
```

</div>

---

### 3. 工具调用记录

<div class="demo-block tool">

**工具调用块**

#### 3.1 写入 C 源文件

```text
1. tool_call write grade_stats.c (563 chars)
   tool_result write ok=True exit=None error=None truncated=False
```

说明：Agent 创建并写入 `grade_stats.c`。

---

#### 3.2 编译程序

```text
2. tool_call powershell gcc
   tool_result powershell ok=True exit=0 error=None truncated=False
```

说明：Agent 调用 `gcc` 编译 C 程序，命令成功执行，退出码为 `0`。

---

#### 3.3 一次失败的命令尝试

```text
3. tool_call powershell cmd
   tool_result powershell ok=False exit=None error=WORKSPACE_ESCAPE truncated=False
   /c
```

说明：该次命令触发了工作区安全限制，未执行成功。
这体现了 TJproxy Agent 的路径与命令边界检查机制：不符合策略的命令会被拒绝，而不是直接运行。

---

#### 3.4 正常样例测试

```text
4. tool_call powershell powershell
   tool_result powershell ok=True exit=0 error=None truncated=False
   average: 84.33
   max: 90
   min: 78
```

测试输入：

```text
3 85 90 78
```

测试输出：

```text
average: 84.33
max: 90
min: 78
```

说明：正常输入下，程序成功计算平均分、最高分和最低分。

---

#### 3.5 `n = 0` 边界测试

```text
5. tool_call powershell powershell
   tool_result powershell ok=False exit=1 error=COMMAND_FAILED truncated=False
   n must be positive
```

说明：当学生人数为 `0` 时，程序输出错误提示，并以非零状态码退出。

---

#### 3.6 `n < 0` 边界测试

```text
6. tool_call powershell powershell
   tool_result powershell ok=False exit=1 error=COMMAND_FAILED truncated=False
   n must be positive
```

说明：当学生人数为负数时，程序同样输出错误提示，并以非零状态码退出。

---

#### 3.7 工具调用结束

```text
final rounds=1
```

</div>

---

### 4. Agent 最终输出

<div class="demo-block agent">

**Agent 输出**

已完成。程序 `grade_stats.c` 已编写并编译为 `grade_stats.exe`。

**测试结果：**

1. **正常输入**：`3 85 90 78` → 平均分 `84.33`，最高分 `90`，最低分 `78` ✅
2. **n = 0**：输出 `n must be positive`，返回码 `1` ✅
3. **n = -2**：输出 `n must be positive`，返回码 `1` ✅

所有测试用例均符合预期。程序正确处理了 `n <= 0` 的情况，并输出了平均分、最高分和最低分。

</div>
