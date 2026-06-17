from io import StringIO

from tjproxy_agent.cli import build_parser, interactive_loop
from tjproxy_agent.runner import RunOutcome


class FakeRunner:
    def __init__(self):
        self.tasks = []
        self.clears = 0
        self.audit = None

    def run(self, task):
        self.tasks.append(task)
        return RunOutcome("completed", f"done:{task}", 1)

    def clear_history(self):
        self.clears += 1


def test_interactive_loop_runs_tasks_and_resets_context():
    runner = FakeRunner()
    stdin = StringIO("first\n/new\nsecond\n/exit\n")
    stdout = StringIO()

    code = interactive_loop(runner, stdin=stdin, stdout=stdout)

    assert code == 0
    assert runner.tasks == ["first", "second"]
    assert runner.clears == 1
    assert "done:first" in stdout.getvalue()
    assert "done:second" in stdout.getvalue()


def test_empty_input_does_not_start_task():
    runner = FakeRunner()

    interactive_loop(runner, stdin=StringIO("\n/exit\n"), stdout=StringIO())

    assert runner.tasks == []


def test_keyboard_interrupt_during_task_returns_to_prompt():
    class InterruptingRunner(FakeRunner):
        def run(self, task):
            if not self.tasks:
                self.tasks.append(task)
                raise KeyboardInterrupt
            return super().run(task)

    runner = InterruptingRunner()
    stdout = StringIO()

    code = interactive_loop(
        runner, stdin=StringIO("first\nsecond\n/exit\n"), stdout=stdout
    )

    assert code == 0
    assert runner.tasks == ["first", "second"]
    assert "current task cancelled" in stdout.getvalue()


def test_keyboard_interrupt_at_prompt_exits_130():
    class InterruptingInput:
        def readline(self):
            raise KeyboardInterrupt

    assert (
        interactive_loop(
            FakeRunner(), stdin=InterruptingInput(), stdout=StringIO()
        )
        == 130
    )


def test_audit_output_does_not_print_write_content():
    class AuditingRunner(FakeRunner):
        def run(self, task):
            from tjproxy_agent.protocol import ToolCall

            self.audit(
                (
                    "tool_call",
                    ToolCall(
                        "write", {"path": "secret.txt", "content": "TOP-SECRET"}
                    ),
                )
            )
            self.audit(
                (
                    "tool_result",
                    '{"type":"tool_result","tool":"write","ok":true,'
                    '"exit_code":null,"stdout":"","stderr":"",'
                    '"error_code":null,"truncated":false}',
                )
            )
            return RunOutcome("completed", "done", 1)

    stdout = StringIO()

    interactive_loop(
        AuditingRunner(), stdin=StringIO("task\n/exit\n"), stdout=stdout
    )

    text = stdout.getvalue()
    assert "write secret.txt" in text
    assert "TOP-SECRET" not in text


def test_parser_requires_workspace_and_accepts_config_path():
    args = build_parser().parse_args(
        ["--workspace", "D:/repo", "--config", "custom.toml"]
    )

    assert str(args.workspace).endswith("repo")
    assert str(args.config) == "custom.toml"


def test_parser_accepts_policy_path():
    args = build_parser().parse_args(
        ["--workspace", "D:/repo", "--policy", "agent.policy.toml"]
    )

    assert str(args.policy) == "agent.policy.toml"


def test_parser_accepts_debug_port():
    args = build_parser().parse_args(
        ["--workspace", "D:/repo", "--debug-port", "9876"]
    )

    assert args.debug_port == 9876
    assert args.debug_host == "127.0.0.1"


def test_plain_cli_approves_once_and_replays_pending_tool_call():
    from tjproxy_agent.protocol import ToolCall

    class ApprovalRunner(FakeRunner):
        def __init__(self):
            super().__init__()
            self.pending_approval = (
                "approval-1",
                ToolCall(
                    "powershell",
                    {"pipeline": [{"command": "git", "args": ["reset", "--hard"]}]},
                ),
            )
            self.approved = []

        def approve_pending(self, approval_id):
            self.approved.append(approval_id)
            self.pending_approval = None
            return RunOutcome("completed", "approved and ran", 1)

        def run(self, task):
            return RunOutcome("approval_required", "git reset --hard", 1)

    stdout = StringIO()
    code = interactive_loop(
        ApprovalRunner(),
        stdin=StringIO("danger\nyes\n/exit\n"),
        stdout=stdout,
    )

    assert code == 0
    assert "需要确认" in stdout.getvalue()
    assert "approved and ran" in stdout.getvalue()


def test_plain_cli_rejects_approval_when_user_does_not_type_yes():
    class ApprovalRunner(FakeRunner):
        def __init__(self):
            super().__init__()
            self.pending_approval = ("approval-1", object())

        def reject_pending(self, approval_id):
            self.pending_approval = None
            return RunOutcome("completed", "已拒绝", 1)

        def run(self, task):
            return RunOutcome("approval_required", "git reset --hard", 1)

    stdout = StringIO()
    interactive_loop(
        ApprovalRunner(),
        stdin=StringIO("danger\nno\n/exit\n"),
        stdout=stdout,
    )

    assert "已拒绝" in stdout.getvalue()
