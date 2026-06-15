import json

import pytest

from tjproxy_agent.protocol import (
    FinalResponse,
    ProtocolError,
    ToolCall,
    parse_response,
    protocol_error_message,
    tool_result_message,
)


def test_parse_one_tool_call():
    parsed = parse_response(
        '{"type":"tool_call","tool":"read","arguments":{"path":"README.md"}}'
    )

    assert parsed == ToolCall(tool="read", arguments={"path": "README.md"})


def test_parse_final_inside_one_json_fence():
    parsed = parse_response('```json\n{"type":"final","content":"done"}\n```')

    assert parsed == FinalResponse(content="done")


@pytest.mark.parametrize(
    "text",
    [
        'I will do this: {"type":"final","content":"done"}',
        '{"type":"tool_call","tool":"read","arguments":{},"extra":1}',
        '{"type":"tool_call","tool":"delete","arguments":{}}',
        '[{"type":"final","content":"a"},{"type":"final","content":"b"}]',
        '{not json}',
        '```python\n{"type":"final","content":"done"}\n```',
        '```json\n{"type":"final","content":"done"}\n```\nextra',
    ],
)
def test_reject_invalid_or_ambiguous_output(text):
    with pytest.raises(ProtocolError):
        parse_response(text)


def test_edit_requires_exact_fields():
    with pytest.raises(ProtocolError, match="edit.arguments"):
        parse_response(
            '{"type":"tool_call","tool":"edit",'
            '"arguments":{"path":"a.txt","old_text":"x"}}'
        )


def test_powershell_requires_structured_pipeline():
    parsed = parse_response(
        '{"type":"tool_call","tool":"powershell","arguments":{'
        '"pipeline":[{"command":"git","args":["status","--short"]}]}}'
    )

    assert parsed.arguments["pipeline"][0]["command"] == "git"


@pytest.mark.parametrize(
    "arguments",
    [
        {"pipeline": []},
        {"pipeline": "git status"},
        {"pipeline": [{"command": "git"}]},
        {"pipeline": [{"command": "git", "args": "status"}]},
        {"pipeline": [{"command": "", "args": []}]},
    ],
)
def test_rejects_invalid_powershell_pipeline(arguments):
    text = json.dumps(
        {"type": "tool_call", "tool": "powershell", "arguments": arguments}
    )

    with pytest.raises(ProtocolError, match="powershell"):
        parse_response(text)


def test_protocol_error_and_tool_result_have_stable_envelopes():
    error = json.loads(protocol_error_message("bad JSON"))
    result = json.loads(
        tool_result_message(
            "powershell",
            ok=False,
            stderr="failed",
            exit_code=1,
            error_code="COMMAND_FAILED",
            truncated=True,
        )
    )

    assert error == {"type": "protocol_error", "error": "bad JSON"}
    assert result == {
        "type": "tool_result",
        "tool": "powershell",
        "ok": False,
        "exit_code": 1,
        "stdout": "",
        "stderr": "failed",
        "error_code": "COMMAND_FAILED",
        "truncated": True,
    }
