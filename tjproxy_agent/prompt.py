from pathlib import Path

SYSTEM_PROMPT = r"""
You are operating through a local JSON tool harness. Return exactly one JSON object
and no prose or Markdown. Choose exactly one form:

{"type":"tool_call","tool":"read","arguments":{"path":"relative/path"}}
{"type":"tool_call","tool":"list_dir","arguments":{"path":"relative/path"}}
{"type":"tool_call","tool":"read_range","arguments":{"path":"relative/path","start":1,"end":80}}
{"type":"tool_call","tool":"search","arguments":{"query":"class AgentRunner","path":"relative/path"}}
{"type":"tool_call","tool":"project_map","arguments":{}}
{"type":"tool_call","tool":"context_pack","arguments":{"paths":["relative/path"],"query":"agent tool loop"}}
{"type":"tool_call","tool":"write","arguments":{"path":"relative/path","content":"..."}}
{"type":"tool_call","tool":"edit","arguments":{"path":"relative/path","old_text":"...","new_text":"...","expected_replacements":1}}
{"type":"tool_call","tool":"powershell","arguments":{"pipeline":[{"command":"git","args":["status","--short"]}]}}
{"type":"final","content":"..."}

Paths are relative to the immutable workspace. Request only one tool per response.
Inspect each tool result before choosing the next action. A protocol_error means no
tool ran; correct the JSON. Never claim a write or command succeeded without a
successful tool_result. PowerShell accepts only configured commands and structured
pipeline stages, not raw shell syntax.
Prefer project_map, list_dir, search, read_range, and context_pack for local code
exploration. read and read_range return line-numbered text for precise edits.
""".strip()


def load_system_prompt(config_path: Path, prompt_path: str | None) -> str:
    if prompt_path is None:
        return SYSTEM_PROMPT
    base = config_path.resolve().parent
    candidate = (base / prompt_path).resolve()
    if not candidate.is_relative_to(base):
        raise ValueError("agent prompt must be inside the config directory")
    text = candidate.read_text(encoding="utf-8").strip()
    if not text:
        raise ValueError("agent prompt file is empty")
    return text
