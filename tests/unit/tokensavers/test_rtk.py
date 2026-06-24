from janus.canonical.models import CanonicalRequest, Message, Role, TextPart, ToolResult
from janus.tokensavers.rtk import (
    RTKSaver,
    compress_git_diff,
    compress_listing,
    dedup_lines,
    smart_truncate,
    strip_ansi,
)


def test_strip_ansi():
    assert strip_ansi("\x1b[32mgreen\x1b[0m text") == "green text"
    assert strip_ansi("no codes here") == "no codes here"


def test_compress_git_diff_strips_mode():
    diff = (
        "diff --git a/foo.py b/foo.py\n"
        "index 1234567..89abcde 100644\n"
        "--- a/foo.py\n+++ b/foo.py\n"
        "@@ -1,3 +1,3 @@\n-old line\n+new line\n unchanged\n"
    )
    result = compress_git_diff(diff)
    assert "index 1234567" not in result
    assert "old line" in result
    assert "new line" in result


def test_compress_git_diff_bigger_returns_original():
    tiny = "diff\n"
    result = compress_git_diff(tiny)
    assert result == tiny


def test_compress_listing_strips_permissions():
    listing = (
        "drwxr-xr-x  2 user user 4096 Jun 24 src\n"
        "-rw-r--r--  1 user user  100 Jun 24 main.py\n"
        "-rw-r--r--  1 user user  200 Jun 24 util.py"
    )
    result = compress_listing(listing)
    assert "drwxr-xr-x" not in result
    assert "main.py" in result


def test_dedup_lines():
    lines = "error: failed\nerror: failed\nwarning: ok\n"
    result = dedup_lines(lines)
    assert result.count("error: failed") == 1
    assert "warning: ok" in result


def test_smart_truncate():
    long_text = "line\n" * 1000
    result = smart_truncate(long_text, max_chars=100)
    assert len(result) <= 200
    assert "truncated" in result.lower()


def test_smart_truncate_short_text_unchanged():
    result = smart_truncate("short", max_chars=100)
    assert result == "short"


def test_rtk_saver_compresses_tool_result():
    long_diff = (
        "diff --git a/f.py b/f.py\nindex 111..222 100644\n--- a/f.py\n+++ b/f.py\n" + "line\n" * 200
    )
    req = CanonicalRequest(
        model="m",
        messages=[
            Message(
                role=Role.TOOL,
                content=[ToolResult(type="tool_result", tool_use_id="t1", content=long_diff)],
            ),
            Message(role=Role.USER, content="fix it"),
        ],
    )
    saver = RTKSaver()
    result = saver.transform(req)
    tool_content = result.messages[0].content[0]
    assert isinstance(tool_content, ToolResult)
    assert len(tool_content.content) < len(long_diff)


def test_rtk_saver_skips_non_tool_results():
    req = CanonicalRequest(
        model="m",
        messages=[Message(role=Role.USER, content=[TextPart(type="text", text="hello")])],
    )
    saver = RTKSaver()
    result = saver.transform(req)
    text_part = result.messages[0].content[0]
    assert isinstance(text_part, TextPart)
    assert text_part.text == "hello"
