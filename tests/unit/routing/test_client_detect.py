from janus.routing.client_detect import detect_client_tool, is_native_passthrough


def test_detect_claude_code():
    assert detect_client_tool({"user-agent": "claude-cli/1.0"}) == "claude"
    assert detect_client_tool({"User-Agent": "claude-code/2.0"}) == "claude"
    assert detect_client_tool({"x-app": "cli"}) == "claude"


def test_detect_codex():
    assert detect_client_tool({"user-agent": "codex-cli/0.1"}) == "codex"


def test_detect_gemini_cli():
    assert detect_client_tool({"user-agent": "gemini-cli/1"}) == "gemini-cli"


def test_detect_antigravity_body():
    assert detect_client_tool({}, {"userAgent": "antigravity"}) == "antigravity"


def test_detect_copilot():
    assert detect_client_tool({"user-agent": "GitHubCopilotChat/0.26"}) == "github-copilot"


def test_native_passthrough_pairs():
    assert is_native_passthrough("claude", "anthropic")
    assert is_native_passthrough("claude", "anthropic-compatible-foo")
    assert not is_native_passthrough("claude", "openai")
    assert is_native_passthrough("codex", "codex")
    assert is_native_passthrough("gemini-cli", "gemini")
    assert not is_native_passthrough(None, "openai")
