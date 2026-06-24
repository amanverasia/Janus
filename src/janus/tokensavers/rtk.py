from __future__ import annotations

import logging
import re

from janus.canonical.models import CanonicalRequest, ToolResult

logger = logging.getLogger(__name__)

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


def strip_ansi(text: str) -> str:
    return _ANSI_RE.sub("", text)


def compress_git_diff(text: str) -> str:
    result = _DIFF_MODE_RE.sub("", text)
    result = re.sub(r"\n{3,}", "\n\n", result)
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


def smart_truncate(text: str, max_chars: int = 8000) -> str:
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


def _detect_and_compress(text: str) -> str:
    if not text or len(text) < 50:
        return text
    result = strip_ansi(text)
    if "diff --git" in result[:200] or result.startswith("diff "):
        result = compress_git_diff(result)
    elif re.search(r"^[dls-][rwxst-]{9}\s", result, re.MULTILINE):
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
                    try:
                        compressed = _detect_and_compress(part.content)
                        msg.content[i] = ToolResult(
                            type="tool_result",
                            tool_use_id=part.tool_use_id,
                            content=compressed,
                        )
                    except Exception as e:
                        logger.warning("RTK compression failed: %s", e)
        return req
