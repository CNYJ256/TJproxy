from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal, Vertical, VerticalScroll
from textual.events import Key
from textual.widgets import Footer, Static, TextArea

from .client import ServiceError
from .runner import RunOutcome
from .tui_support import (
    AgentUiState,
    TuiEventFormatter,
    handle_slash_command,
    prepare_task,
)


class AgentInput(TextArea):
    async def _on_key(self, event: Key) -> None:
        app = self.app
        if getattr(app, "_pending_approval_id", None) is not None:
            if event.key == "y":
                event.prevent_default()
                event.stop()
                app.action_approve_pending()
                return
            if event.key == "n":
                event.prevent_default()
                event.stop()
                app.action_reject_pending()
                return
        await super()._on_key(event)


class AgentTuiApp(App[None]):
    TITLE = "TJproxy Agent"
    CSS = """
    Screen {
        background: #05080f;
        color: #d8e8ff;
    }

    #top {
        height: 8;
        border: round #1f7aff;
        background: #07111f;
        padding: 1 2;
    }

    #brand {
        width: 32;
        content-align: center middle;
        border-right: solid #1f7aff;
        color: #e7f1ff;
    }

    #brand-title {
        text-style: bold;
        color: #6bb6ff;
    }

    #status-panel {
        padding-left: 3;
        content-align: left middle;
        color: #d8e8ff;
    }

    #log {
        height: 1fr;
        border: tall #123c70;
        background: #02050a;
        padding: 1;
    }

    #input {
        height: 6;
        border: tall #2b84ff;
        background: #06101d;
        color: #f5faff;
    }

    #modebar {
        height: 1;
        color: #8cbfff;
        background: #05080f;
    }
    """
    BINDINGS = [
        Binding("f10", "submit", "提交", show=True, priority=True),
        Binding("ctrl+s", "submit", "提交", show=True, priority=True),
        Binding("ctrl+enter", "submit", "提交", show=True, priority=True),
        Binding("f4", "paste_to_input", "粘贴", show=True, priority=True),
        Binding("ctrl+v", "paste_to_input", "粘贴", show=False, priority=True),
        Binding("f9", "copy_output", "复制输出", show=True, priority=True),
        Binding("f5", "approve_pending", "批准一次", show=True, priority=True),
        Binding("y", "approve_pending", "批准一次", show=False, priority=True),
        Binding("escape", "reject_pending", "拒绝", show=True, priority=True),
        Binding("n", "reject_pending", "拒绝", show=False, priority=True),
        Binding("d", "toggle_approval_details", "详情", show=False),
        Binding("ctrl+c", "interrupt", "中断", show=True),
        Binding("ctrl+d", "quit", "退出", show=True),
        Binding("ctrl+p", "history_previous", "Previous", show=False),
        Binding("ctrl+n", "history_next", "Next", show=False),
    ]

    def __init__(
        self,
        runner: Any,
        tools: Any,
        *,
        workspace: Path,
    ):
        super().__init__()
        self.runner = runner
        self.tools = tools
        self.state = AgentUiState(workspace=workspace)
        self.formatter = TuiEventFormatter()
        self.current_worker = None
        self._run_id = 0
        self._visible_run_id = 0
        self._history: list[str] = []
        self._history_index: int | None = None
        self._transcript: list[str] = []
        self._tool_block: Static | None = None
        self._tool_entries: list[str] = []
        self._pending_tool_index: int | None = None
        self._pending_tool_call_plain = ""
        self._approval_card: Static | None = None
        self._approval_details_visible = False
        self._pending_approval_id: str | None = None
        self._pending_approval_raw: dict[str, Any] = {}

    @property
    def policy_profile(self) -> str:
        return "plan" if self.state.mode == "plan" else "dev"

    @property
    def status_text(self) -> str:
        return (
            f"工作区: {self.state.workspace}\n"
            f"模式: {self.state.mode}    累计轮数: {self.state.rounds}\n"
            "提交: F10 / Ctrl+S    换行: Enter    帮助: /help"
        )

    def compose(self) -> ComposeResult:
        with Vertical():
            with Horizontal(id="top"):
                with Container(id="brand"):
                    yield Static("TJproxy", id="brand-title")
                    yield Static("欢迎回来")
                    yield Static("基于TJ Agent平台构建的Agent工具")
                yield Static(self.status_text, id="status-panel")
            yield VerticalScroll(id="log")
            yield AgentInput(
                id="input",
                show_line_numbers=False,
                placeholder="输入任务；Enter 换行，F10 或 Ctrl+S 提交",
            )
            yield Static(self._modebar_text(), id="modebar")
            yield Footer()

    def on_mount(self) -> None:
        self.runner.audit = self._audit_from_runner
        self._write_log("[blue]TJproxy Agent TUI[/blue]", plain="TJproxy Agent TUI")
        self._write_log(
            "使用 /help 查看命令。Enter 换行，F10 或 Ctrl+S 提交多行输入。",
            plain="使用 /help 查看命令。Enter 换行，F10 或 Ctrl+S 提交多行输入。",
        )
        self._input().focus()

    def action_submit(self) -> None:
        text = self._input().text.strip()
        if not text:
            return
        self._input().load_text("")
        self._history.append(text)
        self._history_index = None
        if text.startswith("/"):
            self._handle_command(text)
            return
        if self.state.running:
            self._write_log(
                "[yellow]已有任务正在运行。按 Ctrl+C 可中断当前任务。[/yellow]",
                plain="已有任务正在运行。按 Ctrl+C 可中断当前任务。",
            )
            return
        self._start_task(text)

    def action_paste_to_input(self) -> None:
        self._input().focus()
        self._input().action_paste()

    def action_copy_output(self) -> None:
        text = "\n".join(self._transcript).strip()
        if not text:
            self._write_log("[yellow]当前没有可复制的输出。[/yellow]", plain="当前没有可复制的输出。")
            return
        self.copy_to_clipboard(text)
        self._write_log("[cyan]已复制完整输出到剪贴板。[/cyan]", plain="已复制完整输出到剪贴板。")

    def on_key(self, event: Key) -> None:
        if self._pending_approval_id is None:
            return
        if event.key == "y":
            event.prevent_default()
            event.stop()
            self.action_approve_pending()
        elif event.key == "n":
            event.prevent_default()
            event.stop()
            self.action_reject_pending()

    def action_interrupt(self) -> None:
        if not self.state.running or self.current_worker is None:
            self._write_log("[dim]当前没有正在运行的任务。[/dim]", plain="当前没有正在运行的任务。")
            return
        self._visible_run_id += 1
        self.current_worker.cancel()
        self.state.running = False
        self._refresh_status()
        self._write_log("[yellow]已请求中断当前任务。[/yellow]", plain="已请求中断当前任务。")

    def action_history_previous(self) -> None:
        if not self._history:
            return
        if self._history_index is None:
            self._history_index = len(self._history) - 1
        else:
            self._history_index = max(0, self._history_index - 1)
        self._input().load_text(self._history[self._history_index])

    def action_history_next(self) -> None:
        if self._history_index is None:
            return
        self._history_index += 1
        if self._history_index >= len(self._history):
            self._history_index = None
            self._input().load_text("")
            return
        self._input().load_text(self._history[self._history_index])

    def _start_task(self, task: str) -> None:
        self._run_id += 1
        self._visible_run_id = self._run_id
        run_id = self._run_id
        self.state.running = True
        self.runner.policy_profile = self.policy_profile
        self._refresh_status()
        self._write_log(f"[bold blue]用户[/bold blue] {task}", plain=f"用户 {task}")
        self._reset_tool_block_state()
        prepared = prepare_task(task, plan_mode=self.state.mode == "plan")
        self.current_worker = self.run_worker(
            lambda: self._run_task(run_id, prepared),
            thread=True,
            exclusive=True,
            exit_on_error=False,
        )

    def _run_task(self, run_id: int, task: str) -> None:
        try:
            outcome = self.runner.run(task)
        except KeyboardInterrupt:
            self.call_from_thread(self._finish_interrupted, run_id)
        except ServiceError as exc:
            self.call_from_thread(self._finish_error, run_id, f"service error: {exc}")
        except BaseException as exc:
            self.call_from_thread(self._finish_error, run_id, f"task failed: {exc}")
        else:
            self.call_from_thread(self._finish_outcome, run_id, outcome)

    def _finish_outcome(self, run_id: int, outcome: RunOutcome) -> None:
        if run_id != self._visible_run_id:
            return
        if outcome.status == "approval_required":
            self.state.running = False
            self._refresh_status()
            self._show_approval_card(outcome.content)
            return
        self.state.rounds += outcome.rounds
        self.state.running = False
        self._refresh_status()
        line = self.formatter.format_outcome(outcome)
        self._write_log(line, plain=line.plain)

    def _finish_error(self, run_id: int, message: str) -> None:
        if run_id != self._visible_run_id:
            return
        self.state.running = False
        self._refresh_status()
        self._write_log(f"[red]{message}[/red]", plain=message)

    def _finish_interrupted(self, run_id: int) -> None:
        if run_id != self._visible_run_id:
            return
        self.state.running = False
        self._refresh_status()
        self._write_log("[yellow]当前任务已取消[/yellow]", plain="当前任务已取消")

    def _audit_from_runner(self, event: tuple[str, object]) -> None:
        run_id = self._run_id
        self.call_from_thread(self._append_audit, run_id, event)

    def _append_audit(self, run_id: int, event: tuple[str, object]) -> None:
        if run_id != self._visible_run_id:
            return
        kind, value = event
        if kind == "tool_call":
            self._start_tool_block(event)
            return
        if kind == "tool_result":
            self._finish_tool_block(event)
            return
        line = self.formatter.format_audit(event)
        self._write_log(line, plain=line.plain)

    def _handle_command(self, command: str) -> None:
        result = handle_slash_command(command, self.state, self.runner)
        self.runner.policy_profile = self.policy_profile
        if result.kind == "exit":
            self.exit()
            return
        if result.kind == "clear":
            self._log().remove_children()
            self._reset_tool_block_state()
            self._write_log("[blue]屏幕已清空[/blue]", plain="屏幕已清空")
            return
        if result.kind == "copy_output":
            self.action_copy_output()
            return
        self._write_log(f"[cyan]{result.message}[/cyan]", plain=result.message)
        self._refresh_status()

    def _show_approval_card(self, raw: str) -> None:
        try:
            result = json.loads(raw)
        except json.JSONDecodeError:
            result = {}
        metadata = result.get("metadata", {}) if isinstance(result, dict) else {}
        approval_id = metadata.get("approval_id") if isinstance(metadata, dict) else None
        self._pending_approval_id = approval_id if isinstance(approval_id, str) else None
        self._pending_approval_raw = result
        risk = metadata.get("risk") if isinstance(metadata, dict) else None
        text = Text("需要确认\n", style="bold yellow")
        text.append(str(result.get("stdout", "")), style="white")
        text.append("\n风险：", style="yellow")
        text.append(str(risk or "unknown"), style="white")
        text.append("\n原因：", style="yellow")
        text.append(str(result.get("stderr", "")), style="white")
        text.append("\n影响范围：", style="yellow")
        text.append("当前 workspace", style="white")
        text.append("\n操作：y/F5 批准一次 / n/Esc 拒绝 / d 展开详情", style="cyan")
        if self._approval_card is not None:
            self._approval_card.remove()
        self._approval_card = Static(text, classes="approval-card")
        self._approval_card.plain_text = text.plain
        self._log().mount(self._approval_card)
        self._transcript.append(text.plain)

    def action_approve_pending(self) -> None:
        if self._pending_approval_id is None:
            self._write_log("[yellow]当前没有待批准操作。[/yellow]", plain="当前没有待批准操作。")
            return
        outcome = self.runner.approve_pending(self._pending_approval_id)
        self._pending_approval_id = None
        if self._approval_card is not None:
            self._approval_card.remove()
            self._approval_card = None
        self._finish_outcome(self._visible_run_id, outcome)

    def action_reject_pending(self) -> None:
        if self._pending_approval_id is None:
            return
        outcome = self.runner.reject_pending(self._pending_approval_id)
        self._pending_approval_id = None
        if self._approval_card is not None:
            self._approval_card.remove()
            self._approval_card = None
        self._finish_outcome(self._visible_run_id, outcome)

    def action_toggle_approval_details(self) -> None:
        if self._approval_card is None:
            return
        self._approval_details_visible = not self._approval_details_visible
        if not self._approval_details_visible:
            self._show_approval_card(
                json.dumps(self._pending_approval_raw, ensure_ascii=False)
            )
            return
        text = Text("审批详情\n", style="bold yellow")
        text.append(
            json.dumps(self._pending_approval_raw, ensure_ascii=False, indent=2),
            style="white",
        )
        self._approval_card.update(text)
        self._approval_card.plain_text = text.plain

    def _refresh_status(self) -> None:
        self.query_one("#status-panel", Static).update(self.status_text)
        self.query_one("#modebar", Static).update(self._modebar_text())

    def _modebar_text(self) -> str:
        return (
            f">> {self.state.mode} 模式"
            " | F10/Ctrl+S 提交 | F4/Ctrl+V 粘贴 | F9 复制输出 | Ctrl+C 中断 | Ctrl+D 退出"
        )

    def _write_log(self, renderable: object, *, plain: str | None = None) -> None:
        widget = Static(renderable, classes="log-entry")
        widget.plain_text = self._plain_text(renderable, plain)
        self._log().mount(widget)
        self._log().scroll_end(animate=False)
        if plain is not None:
            self._transcript.append(plain)
        elif isinstance(renderable, Text):
            self._transcript.append(renderable.plain)
        else:
            self._transcript.append(str(renderable))

    def _start_tool_block(self, event: tuple[str, object]) -> None:
        line = self.formatter.format_audit(event)
        self._pending_tool_call_plain = line.plain
        self._tool_entries.append(
            f"{len(self._tool_entries) + 1}. {line.plain}\n   状态：等待结果"
        )
        self._pending_tool_index = len(self._tool_entries) - 1
        self._refresh_tool_block()

    def _finish_tool_block(self, event: tuple[str, object]) -> None:
        result = self.formatter.format_audit(event)
        if self._pending_tool_index is None:
            self._tool_entries.append(
                f"{len(self._tool_entries) + 1}. {result.plain}"
            )
            self._refresh_tool_block()
            return
        self._tool_entries[self._pending_tool_index] = (
            f"{self._pending_tool_index + 1}. {self._pending_tool_call_plain}\n"
            f"{_indent(result.plain)}"
        )
        self._pending_tool_index = None
        self._pending_tool_call_plain = ""
        self._refresh_tool_block()

    def _refresh_tool_block(self) -> None:
        block = Text("工具调用块\n", style="bold cyan")
        block.append("\n\n".join(self._tool_entries), style="bright_blue")
        if self._tool_block is None:
            self._tool_block = Static(block, classes="tool-block")
            self._tool_block.plain_text = block.plain
            self._log().mount(self._tool_block)
        else:
            self._tool_block.update(block)
            self._tool_block.plain_text = block.plain
        self._log().scroll_end(animate=False)
        self._transcript.append(block.plain)

    def _reset_tool_block_state(self) -> None:
        self._tool_block = None
        self._tool_entries = []
        self._pending_tool_index = None
        self._pending_tool_call_plain = ""

    def _plain_text(self, renderable: object, plain: str | None) -> str:
        if plain is not None:
            return plain
        if isinstance(renderable, Text):
            return renderable.plain
        return str(renderable)

    def _log(self) -> VerticalScroll:
        return self.query_one("#log", VerticalScroll)

    def _input(self) -> TextArea:
        return self.query_one("#input", TextArea)


def _indent(value: str) -> str:
    return "\n".join(f"   {line}" for line in value.splitlines())
