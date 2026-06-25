from __future__ import annotations

import logging

from janus.canonical.events import CanonicalEvent, MessageDelta, TextDelta
from janus.canonical.models import Usage
from janus.formats.base import StreamParser

logger = logging.getLogger(__name__)


class StreamUsageTracker:
    """Wraps a StreamParser to collect usage and text as events flow through.

    After the stream completes (or disconnects), call get_usage() to get
    the final Usage: either captured from upstream events, or estimated
    via tiktoken as a fallback.
    """

    def __init__(self, parser: StreamParser) -> None:
        self._parser = parser
        self._input_tokens = 0
        self._output_tokens = 0
        self._cache_creation = 0
        self._cache_read = 0
        self._has_usage = False
        self._text_parts: list[str] = []

    def feed(self, line: str) -> list[CanonicalEvent]:
        events = self._parser.feed(line)
        self._collect(events)
        return events

    def finish(self) -> list[CanonicalEvent]:
        events = self._parser.finish()
        self._collect(events)
        return events

    def _collect(self, events: list[CanonicalEvent]) -> None:
        for event in events:
            if isinstance(event, MessageDelta) and event.usage is not None:
                self._has_usage = True
                if event.usage.input_tokens:
                    self._input_tokens = event.usage.input_tokens
                if event.usage.output_tokens:
                    self._output_tokens = event.usage.output_tokens
                if event.usage.cache_creation_input_tokens:
                    self._cache_creation = event.usage.cache_creation_input_tokens
                if event.usage.cache_read_input_tokens:
                    self._cache_read = event.usage.cache_read_input_tokens
            if isinstance(event, TextDelta):
                self._text_parts.append(event.text)

    def get_usage(self) -> Usage:
        if self._has_usage:
            return Usage(
                input_tokens=self._input_tokens,
                output_tokens=self._output_tokens,
                cache_creation_input_tokens=self._cache_creation,
                cache_read_input_tokens=self._cache_read,
            )
        return _estimate_usage("".join(self._text_parts))


def _estimate_usage(text: str) -> Usage:
    try:
        import tiktoken

        enc = tiktoken.get_encoding("cl100k_base")
        return Usage(output_tokens=len(enc.encode(text)))
    except Exception as e:
        logger.debug("tiktoken estimation failed: %s", e)
        return Usage(output_tokens=len(text) // 4)
