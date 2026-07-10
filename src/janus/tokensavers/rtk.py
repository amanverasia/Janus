from __future__ import annotations

import logging
import re

from janus.canonical.models import CanonicalRequest, ToolResult

logger = logging.getLogger(__name__)

# --- constants (mirrors 9router's rtk/constants.js) ---------------------------------
RAW_CAP = 10 * 1024 * 1024  # 10 MiB; pass through untouched above this size
MIN_COMPRESS_SIZE = 500  # bytes; skip anything smaller than this
DETECT_WINDOW = 1024  # autodetect peeks at first N chars only
GIT_DIFF_HUNK_MAX_LINES = 100  # per-hunk line cap
GIT_LOG_MAX_LINES = 200  # git log output line cap
GREP_PER_FILE_MAX = 10  # matches kept per file in grep output
FIND_PER_DIR_MAX = 10  # paths kept per dir in find output
TREE_MAX_LINES = 200  # lines kept from `tree` output
STATUS_MAX_FILES = 10  # files kept per category in git status output
SMART_TRUNCATE_HEAD = 120  # lines kept from the top when line-truncating
SMART_TRUNCATE_TAIL = 60  # lines kept from the bottom when line-truncating
SMART_TRUNCATE_MIN_LINES = 250  # line-based truncation only kicks in above this

_GIT_LOG_COMMIT_MAX_FULL_BODY = 20  # commits beyond this get single-line bodies

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")
_DIFF_MODE_RE = re.compile(
    r"^(index |old mode |new mode |similarity index |copy from |copy to |"
    r"rename from |rename to |deleted file |new file mode ).*$",
    re.MULTILINE,
)
_PERMISSIONS_RE = re.compile(
    r"^[\s]*[dls-][rwxst-]{9}\s+(?:\d+\s+)?(?:\S+\s+)?\S+\s+\S+\s+",
    re.MULTILINE,
)
_TRUNCATE_MARKER = "\n[…truncated…]"

_GIT_LOG_DETECT_RE = re.compile(r"^commit [0-9a-f]{7,40}", re.MULTILINE)
# One porcelain-shaped line: a status code then a single path-like token (no
# spaces). Detection requires several of these (see _is_git_status) so prose
# that merely starts with "A " can't misroute to the git-status filter.
_GIT_STATUS_LINE_RE = re.compile(r"^(\?\?|[MADRC][MD ]?)\s+\S+$")
_GIT_STATUS_MIN_LINES = 3  # porcelain-shaped lines needed to classify as git status
_BUILD_OUTPUT_DETECT_RE = re.compile(r"error\[|warning:|FAILED|BUILD")
# A grep match line `path:NN:…`. The pre-colon token must contain at least one
# non-digit so pure timestamps (`12:34:56 …`) don't look like grep matches.
_GREP_LINE_RE = re.compile(r"^[^\s:]*[^\d\s:][^\s:]*:\d+[:\s]")
# A grep -C/-A/-B context line `path-NN-…` (same non-digit token requirement).
_GREP_CONTEXT_RE = re.compile(r"^[^\s:]*[^\d\s:][^\s:]*-\d+-")
_TREE_GLYPH_RE = re.compile(r"├──|└──")
_LS_ROW_RE = re.compile(r"^[dls-][rwxst-]{9}\s", re.MULTILINE)
_BUILD_LINE_RE = re.compile(r"error|warning|failed|FAILED|✗|✘")
_COMMIT_LINE_RE = re.compile(r"^commit [0-9a-f]{7,40}")
_STATUS_MODIFIED_RE = re.compile(r"^[MADRC]\s")
_STATUS_UNTRACKED_RE = re.compile(r"^\?\?\s")


def strip_ansi(text: str) -> str:
    return _ANSI_RE.sub("", text)


def _cap_diff_hunks(text: str) -> str:
    lines = text.split("\n")
    out: list[str] = []
    in_hunk = False
    hunk_shown = 0
    hunk_skipped = 0

    def flush_skip() -> None:
        nonlocal hunk_skipped
        if hunk_skipped:
            out.append(f"  … ({hunk_skipped} lines truncated)")
            hunk_skipped = 0

    for line in lines:
        if line.startswith("diff --git"):
            flush_skip()
            in_hunk = False
            out.append(line)
            continue
        if line.startswith("@@"):
            flush_skip()
            in_hunk = True
            hunk_shown = 0
            out.append(line)
            continue
        if in_hunk and line[:1] in ("+", "-", " "):
            if hunk_shown < GIT_DIFF_HUNK_MAX_LINES:
                out.append(line)
                hunk_shown += 1
            else:
                hunk_skipped += 1
            continue
        out.append(line)
    flush_skip()
    return "\n".join(out)


def compress_git_diff(text: str) -> str:
    result = _DIFF_MODE_RE.sub("", text)
    result = re.sub(r"\n{3,}", "\n\n", result)
    result = _cap_diff_hunks(result)
    if len(result) >= len(text):
        return text
    return result


def compress_listing(text: str) -> str:
    result = _PERMISSIONS_RE.sub("", text)
    result = re.sub(r"\n{3,}", "\n\n", result)
    if len(result) >= len(text):
        return text
    return result


def dedup_lines(text: str) -> str:
    lines = text.split("\n")
    seen: set[str] = set()
    result: list[str] = []
    for line in lines:
        if line not in seen:
            seen.add(line)
            result.append(line)
    output = "\n".join(result)
    if len(output) >= len(text):
        return text
    return output


def compress_git_log(text: str, max_lines: int = GIT_LOG_MAX_LINES) -> str:
    """Keep commit headers/metadata, collapse bodies of commits past the 20th to 1 line."""
    lines = text.split("\n")
    out: list[str] = []
    truncated_lines = 0
    commit_idx = -1
    in_commit = False
    body_lines_seen = 0

    def emit(line: str) -> None:
        nonlocal truncated_lines
        if len(out) < max_lines:
            out.append(line)
        else:
            truncated_lines += 1

    for line in lines:
        if _COMMIT_LINE_RE.match(line):
            commit_idx += 1
            in_commit = True
            body_lines_seen = 0
            emit(line)
            continue
        if in_commit:
            stripped = line.strip()
            if stripped.startswith("Author:") or stripped.startswith("Date:"):
                emit(line)
                continue
            if not stripped:
                continue
            if commit_idx >= _GIT_LOG_COMMIT_MAX_FULL_BODY:
                if body_lines_seen == 0:
                    emit(line)
                body_lines_seen += 1
                continue
            emit(line)
            body_lines_seen += 1
            continue
        emit(line)

    if truncated_lines:
        out.append(f"… ({truncated_lines} more lines)")

    result = "\n".join(out)
    if not result:
        return text
    if len(result) >= len(text):
        return text
    return result


def compress_git_status(text: str, max_files: int = STATUS_MAX_FILES) -> str:
    """Cap modified/untracked file lists in `git status` (porcelain-style) output."""
    lines = text.split("\n")
    mod_total = sum(1 for line in lines if _STATUS_MODIFIED_RE.match(line))
    unt_total = sum(1 for line in lines if _STATUS_UNTRACKED_RE.match(line))

    out: list[str] = []
    mod_count = 0
    unt_count = 0
    for line in lines:
        if _STATUS_UNTRACKED_RE.match(line):
            unt_count += 1
            if unt_count <= max_files:
                out.append(line)
            continue
        if _STATUS_MODIFIED_RE.match(line):
            mod_count += 1
            if mod_count <= max_files:
                out.append(line)
            continue
        out.append(line)

    if mod_total > max_files:
        out.append(f"… (+{mod_total - max_files} more modified)")
    if unt_total > max_files:
        out.append(f"… (+{unt_total - max_files} more untracked)")

    result = "\n".join(out)
    if not result:
        return text
    if len(result) >= len(text):
        return text
    return result


def _grep_match_file(line: str) -> str | None:
    """Return the file path when `line` is a `path:NN:…` grep match, else None."""
    if not _GREP_LINE_RE.match(line):
        return None
    first = line.find(":")
    second = line.find(":", first + 1)
    if second == -1:
        return None
    if not line[first + 1 : second].isdigit():
        return None
    return line[:first]


def compress_grep_output(text: str, per_file_max: int = GREP_PER_FILE_MAX) -> str:
    """Cap `path:line:content` matches per file, leaving all other lines intact.

    Non-matching lines (tracebacks, `-C` context lines, `--` separators, prose)
    pass through unchanged in their original positions; only match lines beyond
    `per_file_max` for a given file are dropped, replaced by one
    "+N more in <file>" marker at the point of the first dropped match.
    """
    lines = text.split("\n")
    totals: dict[str, int] = {}
    for line in lines:
        file = _grep_match_file(line)
        if file is not None:
            totals[file] = totals.get(file, 0) + 1

    if not totals:
        return text

    out: list[str] = []
    seen: dict[str, int] = {}
    for line in lines:
        file = _grep_match_file(line)
        if file is None:
            out.append(line)
            continue
        seen[file] = seen.get(file, 0) + 1
        if seen[file] <= per_file_max:
            out.append(line)
        elif seen[file] == per_file_max + 1:
            out.append(f"… (+{totals[file] - per_file_max} more in {file})")

    result = "\n".join(out)
    if not result:
        return text
    if len(result) >= len(text):
        return text
    return result


def compress_find_output(text: str, per_dir_max: int = FIND_PER_DIR_MAX) -> str:
    """Group bare file paths by parent dir, capping entries shown per dir."""
    lines = [line for line in text.split("\n") if line.strip()]
    if not lines:
        return text

    by_dir: dict[str, list[str]] = {}
    for path in lines:
        idx = path.rfind("/")
        if idx == -1:
            directory, basename = ".", path
        else:
            directory, basename = path[:idx] or "/", path[idx + 1 :]
        by_dir.setdefault(directory, []).append(basename)

    out: list[str] = []
    for directory in sorted(by_dir):
        files = by_dir[directory]
        out.append(f"{directory}/ ({len(files)})")
        out.extend(f"  {f}" for f in files[:per_dir_max])
        if len(files) > per_dir_max:
            out.append(f"  … (+{len(files) - per_dir_max} more)")

    result = "\n".join(out)
    if not result:
        return text
    if len(result) >= len(text):
        return text
    return result


def compress_tree_output(text: str, max_lines: int = TREE_MAX_LINES) -> str:
    """Keep the first max_lines of `tree` output plus a summary of what was cut."""
    lines = text.split("\n")
    if len(lines) <= max_lines:
        return text
    kept = lines[:max_lines]
    cut = len(lines) - len(kept)
    result = "\n".join(kept) + f"\n… (+{cut} more lines)"
    if len(result) >= len(text):
        return text
    return result


def compress_build_output(text: str) -> str:
    """Keep error/warning/failure lines plus 3 lines of context, plus the last 30 lines."""
    lines = text.split("\n")
    keep_idx: set[int] = set()
    for i, line in enumerate(lines):
        if _BUILD_LINE_RE.search(line):
            for j in range(max(0, i - 3), min(len(lines), i + 4)):
                keep_idx.add(j)
    tail_start = max(0, len(lines) - 30)
    keep_idx.update(range(tail_start, len(lines)))

    if not keep_idx:
        return text

    out: list[str] = []
    prev = -2
    for i in sorted(keep_idx):
        if i != prev + 1 and out:
            out.append("…")
        out.append(lines[i])
        prev = i

    result = "\n".join(out)
    if len(result) >= len(text):
        return text
    return result


def smart_truncate(text: str, max_chars: int = 8000) -> str:
    lines = text.split("\n")
    if len(lines) > SMART_TRUNCATE_MIN_LINES:
        head = lines[:SMART_TRUNCATE_HEAD]
        tail = lines[len(lines) - SMART_TRUNCATE_TAIL :]
        cut = len(lines) - len(head) - len(tail)
        result = "\n".join(head) + f"\n[… {cut} lines truncated …]\n" + "\n".join(tail)
        if len(result) >= len(text):
            return text
        return result

    if len(text) <= max_chars:
        return text
    truncated = text[:max_chars]
    last_space = truncated.rfind(" ")
    if last_space > max_chars * 0.8:
        truncated = truncated[:last_space]
    return truncated + _TRUNCATE_MARKER


def _looks_like_log(text: str) -> bool:
    first_1k = text[:1024]
    log_patterns = [r"\d{4}-\d{2}-\d{2}", r"\d{2}:\d{2}:\d{2}", r"ERROR|WARN|INFO|DEBUG|TRACE"]
    return any(re.search(p, first_1k) for p in log_patterns)


def _is_git_status(window: str) -> bool:
    lines = [line for line in window.split("\n") if line.strip()]
    count = sum(1 for line in lines if _GIT_STATUS_LINE_RE.match(line))
    return count >= _GIT_STATUS_MIN_LINES


def _is_grep_output(window: str) -> bool:
    """Grep-shaped: enough match lines AND a majority of the window looks like grep.

    The majority rule keeps mixed content (timestamped logs, tracebacks with a
    few `file.py:NN:` frames) out of the grep filter; `-C` context lines and
    `--` separators count toward the majority since real grep output has them.
    """
    lines = [line for line in window.split("\n") if line.strip()]
    if not lines:
        return False
    matches = sum(1 for line in lines if _GREP_LINE_RE.match(line))
    if matches < 5:
        return False
    grep_shaped = sum(
        1
        for line in lines
        if _GREP_LINE_RE.match(line) or _GREP_CONTEXT_RE.match(line) or line == "--"
    )
    return grep_shaped * 2 > len(lines)


def _is_path_like(line: str) -> bool:
    stripped = line.strip()
    if not stripped or " " in stripped:
        return False
    return bool(re.match(r"^\.{0,2}/", stripped)) or "/" in stripped


def _is_find_output(window: str) -> bool:
    lines = [line for line in window.split("\n") if line.strip()]
    if len(lines) < 10:
        return False
    return all(":" not in line and _is_path_like(line) for line in lines)


def _detect_and_compress(text: str) -> str:
    if not text or len(text) < MIN_COMPRESS_SIZE:
        return text
    if len(text) > RAW_CAP:
        return text

    result = strip_ansi(text)
    window = result[:DETECT_WINDOW]

    if _GIT_LOG_DETECT_RE.search(window):
        result = compress_git_log(result)
    elif "diff --git" in window or window.startswith("diff "):
        result = compress_git_diff(result)
    elif _is_git_status(window) or "Changes not staged" in window:
        result = compress_git_status(result)
    elif _BUILD_OUTPUT_DETECT_RE.search(window):
        result = compress_build_output(result)
    elif _is_grep_output(window):
        result = compress_grep_output(result)
    elif _is_find_output(window):
        result = compress_find_output(result)
    elif _TREE_GLYPH_RE.search(window):
        result = compress_tree_output(result)
    elif _LS_ROW_RE.search(window):
        result = compress_listing(result)
    elif len(result.split("\n")) > 50 and _looks_like_log(result):
        result = dedup_lines(result)

    result = smart_truncate(result)
    return result


class RTKSaver:
    def transform(self, req: CanonicalRequest) -> CanonicalRequest:
        for msg in req.messages:
            if not isinstance(msg.content, list):
                continue
            for i, part in enumerate(msg.content):
                if isinstance(part, ToolResult):
                    if part.is_error or not isinstance(part.content, str):
                        continue
                    try:
                        compressed = _detect_and_compress(part.content)
                        msg.content[i] = ToolResult(
                            type="tool_result",
                            tool_use_id=part.tool_use_id,
                            content=compressed,
                            is_error=part.is_error,
                            cache_control=part.cache_control,
                        )
                    except Exception as e:
                        logger.warning("RTK compression failed: %s", e)
        return req
