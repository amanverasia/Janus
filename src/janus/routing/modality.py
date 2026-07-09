"""Strip multimodal content the target model cannot read.

Ported from 9router ``open-sse/translator/concerns/modality.js``.
Operates on ``CanonicalRequest`` so every client format benefits after parse.
"""

from __future__ import annotations

import logging
from typing import Any

from janus.canonical.models import (
    CanonicalRequest,
    ContentPart,
    ImagePart,
    Message,
    TextPart,
)

logger = logging.getLogger(__name__)

_PLACEHOLDER_CURRENT = {
    "vision": "[image omitted: model has no vision support]",
    "pdf": "[file omitted: model has no document support]",
    "audio": "[audio omitted: model has no audio support]",
}
_PLACEHOLDER_PREV = {
    "vision": "[Previous image omitted from context.]",
    "pdf": "[Previous file omitted from context.]",
    "audio": "[Previous audio omitted from context.]",
}


def strip_unsupported_modalities(
    req: CanonicalRequest,
    caps: dict[str, Any],
) -> CanonicalRequest:
    """Drop ImageParts when caps.vision is False; replace with a text placeholder."""
    if not caps or caps.get("vision", True) is not False:
        return req

    if not req.messages:
        return req

    last_idx = len(req.messages) - 1
    new_messages: list[Message] = []
    changed = False

    for i, msg in enumerate(req.messages):
        if not isinstance(msg.content, list):
            new_messages.append(msg)
            continue
        removed: set[str] = set()
        kept: list[ContentPart] = []
        for part in msg.content:
            if isinstance(part, ImagePart):
                removed.add("vision")
                continue
            kept.append(part)
        if not removed:
            new_messages.append(msg)
            continue
        changed = True
        is_last = i == last_idx
        for cap in removed:
            text = (_PLACEHOLDER_CURRENT if is_last else _PLACEHOLDER_PREV)[cap]
            kept.append(TextPart(text=text))
        new_messages.append(msg.model_copy(update={"content": kept}))

    if not changed:
        return req
    logger.debug("Stripped unsupported modalities for model %s", req.model)
    return req.model_copy(update={"messages": new_messages})
