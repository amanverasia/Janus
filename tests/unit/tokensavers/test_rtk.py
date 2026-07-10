from janus.canonical.models import CanonicalRequest, Message, Role, TextPart, ToolResult
from janus.tokensavers.rtk import (
    MIN_COMPRESS_SIZE,
    RAW_CAP,
    RTKSaver,
    _detect_and_compress,
    compress_build_output,
    compress_find_output,
    compress_git_diff,
    compress_git_log,
    compress_git_status,
    compress_grep_output,
    compress_listing,
    compress_tree_output,
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


def test_compress_git_diff_caps_hunk_lines():
    hunk_lines = "\n".join(f"+added line {i}" for i in range(150))
    diff = f"diff --git a/foo.py b/foo.py\n@@ -1,150 +1,150 @@\n{hunk_lines}\n"
    result = compress_git_diff(diff)
    assert len(result) < len(diff)
    assert "lines truncated" in result
    assert "added line 0" in result
    assert "added line 149" not in result  # beyond the 100-line hunk cap


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


def test_smart_truncate_line_based_for_many_lines():
    long_text = "\n".join(f"line {i}" for i in range(400))
    result = smart_truncate(long_text)
    assert len(result) < len(long_text)
    assert "truncated" in result.lower()
    lines = result.split("\n")
    assert lines[0] == "line 0"
    assert lines[-1] == "line 399"
    assert "220 lines truncated" in result


def test_smart_truncate_char_fallback_for_single_line_blob():
    long_text = "x" * 20000
    result = smart_truncate(long_text, max_chars=8000)
    assert len(result) < len(long_text)
    assert "truncated" in result.lower()


def test_smart_truncate_short_text_unchanged():
    result = smart_truncate("short", max_chars=100)
    assert result == "short"


def test_smart_truncate_never_grows_below_line_threshold():
    text = "a\nb\nc\n"
    result = smart_truncate(text, max_chars=100)
    assert result == text


def test_compress_git_log_caps_lines_and_collapses_body():
    blocks = []
    for i in range(60):
        blocks.append(
            f"commit {i:07x}abcdef1234567890\n"
            f"Author: Dev <dev@example.com>\n"
            f"Date:   Mon Jan {i % 28 + 1} 00:00:00 2024\n"
            "\n"
            f"    commit message body line one for {i}\n"
            f"    second body line for {i}\n"
            "\n"
        )
    text = "".join(blocks)
    result = compress_git_log(text)
    assert result != ""
    assert len(result) < len(text)
    assert "more lines" in result
    # bodies of commits beyond the 20th collapse to their first line only
    assert "commit message body line one for 25" in result
    assert "second body line for 25" not in result


def test_compress_git_log_never_empty_on_unmatched_input():
    text = "not a git log at all\njust some text\n"
    result = compress_git_log(text)
    assert result == text


def test_compress_git_status_caps_files_with_more_markers():
    modified = "\n".join(f"M file{i}.txt" for i in range(16))
    untracked = "\n".join(f"?? new{i}.txt" for i in range(16))
    text = modified + "\n" + untracked + "\n"
    result = compress_git_status(text)
    assert len(result) < len(text)
    assert "more modified" in result
    assert "more untracked" in result
    assert "file0.txt" in result
    assert "file15.txt" not in result  # beyond the 10-file cap


def test_compress_grep_output_groups_and_caps_per_file():
    lines = [f"src/a.py:{i}:match number {i}" for i in range(15)]
    lines += [f"src/b.py:{i}:other match {i}" for i in range(3)]
    text = "\n".join(lines)
    result = compress_grep_output(text)
    assert len(result) < len(text)
    assert "more in src/a.py" in result
    assert "src/b.py:0" in result


def test_compress_grep_output_never_empty_on_unmatched_input():
    text = "no colons here\njust plain text\n"
    result = compress_grep_output(text)
    assert result == text


def test_compress_find_output_groups_by_dir_and_caps():
    lines = [f"src/pkg/module{i}.py" for i in range(15)]
    lines += ["src/other/thing.py"]
    text = "\n".join(lines)
    result = compress_find_output(text)
    assert len(result) < len(text)
    assert "more" in result
    assert "module0.py" in result
    assert "module14.py" not in result  # beyond the 10-per-dir cap


def test_find_detection_requires_path_like_lines_not_just_no_colons():
    # Prose lines with no colons and no path separators must NOT be mistaken for
    # `find` output, or compress_find_output would silently drop items beyond
    # FIND_PER_DIR_MAX. Regression for the misdetection bug.
    lines = [
        f"item number {i} in a plain list without any special characters at all here"
        for i in range(12)
    ]
    text = "\n".join(lines)
    assert len(text) >= MIN_COMPRESS_SIZE
    result = _detect_and_compress(text)
    assert result == text
    for i in range(12):
        assert f"item number {i}" in result


def test_find_detection_still_fires_for_genuine_path_listing():
    lines = [f"./src/some/deeper/pkg/directory/module{i}.py" for i in range(15)]
    lines += ["./src/some/deeper/pkg/other/thing.py"]
    text = "\n".join(lines)
    assert len(text) >= MIN_COMPRESS_SIZE
    result = _detect_and_compress(text)
    assert len(result) < len(text)
    assert "module0.py" in result
    assert "module14.py" not in result  # beyond the 10-per-dir cap


def test_compress_tree_output_caps_lines_with_summary():
    lines = [f"├── file{i}.py" for i in range(300)]
    text = "\n".join(lines)
    result = compress_tree_output(text)
    assert len(result) < len(text)
    assert "more lines" in result
    assert "file0.py" in result
    assert "file299.py" not in result


def test_compress_tree_output_short_input_unchanged():
    text = "├── a.py\n└── b.py\n"
    result = compress_tree_output(text)
    assert result == text


def test_compress_build_output_keeps_errors_and_context():
    filler = ["info: nothing interesting happening here"] * 100
    lines = filler[:40] + ["error: something broke badly"] + filler[40:]
    text = "\n".join(lines)
    result = compress_build_output(text)
    assert len(result) < len(text)
    assert "error: something broke badly" in result


def test_compress_build_output_returns_original_when_no_shrink():
    text = "error: only line here\n"
    result = compress_build_output(text)
    assert result == text


def test_min_compress_size_gate_skips_small_input():
    small = "diff --git a/f b/f\nindex 1..2 100644\n"
    assert len(small) < MIN_COMPRESS_SIZE
    result = _detect_and_compress(small)
    assert result == small


def test_raw_cap_passes_through_untouched():
    big = "x" * (RAW_CAP + 1)
    result = _detect_and_compress(big)
    assert result == big


def test_detect_priority_git_log_over_git_diff():
    block = (
        "commit abcdef1234567\nAuthor: a <a@example.com>\nDate: Mon Jan 1 00:00:00 2024\n\n"
        "    subject\n\n"
        "diff --git a/f.py b/f.py\nindex 1234567..89abcde 100644\n--- a/f.py\n+++ b/f.py\n"
        "@@ -1,3 +1,3 @@\n-old\n+new\n"
    )
    text = block * 5
    result = _detect_and_compress(text)
    # git-log filter keeps commit-body lines (incl. the embedded diff header) verbatim,
    # whereas the git-diff filter would have stripped the "index " line entirely.
    assert "index 1234567" in result


def test_detect_priority_git_diff_when_no_commit_header():
    text = (
        "diff --git a/f.py b/f.py\nindex 111..222 100644\n--- a/f.py\n+++ b/f.py\n"
        "@@ -1,3 +1,3 @@\n-old\n+new\n"
    ) + ("x\n" * 300)
    result = _detect_and_compress(text)
    assert "index 111" not in result


def test_filters_never_grow_output():
    from janus.tokensavers import rtk

    filters = [
        rtk.compress_git_log,
        rtk.compress_git_status,
        rtk.compress_grep_output,
        rtk.compress_find_output,
        rtk.compress_tree_output,
        rtk.compress_build_output,
    ]
    tiny = "short text\nwith a few\nlines only\n"
    for f in filters:
        result = f(tiny)
        assert len(result) <= len(tiny)
        assert result  # never empty


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
