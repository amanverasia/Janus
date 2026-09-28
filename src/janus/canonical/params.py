from __future__ import annotations

import logging

from janus.canonical.models import CanonicalRequest

logger = logging.getLogger(__name__)

SAMPLING_PARAMS: tuple[str, ...] = (
    "seed",
    "n",
    "presence_penalty",
    "frequency_penalty",
    "logit_bias",
    "parallel_tool_calls",
    "logprobs",
    "top_logprobs",
)


def log_unsupported_sampling_params(
    req: CanonicalRequest,
    target_format: str,
    supported: frozenset[str] = frozenset(),
) -> None:
    dropped = [
        name
        for name in SAMPLING_PARAMS
        if getattr(req, name) is not None and name not in supported
    ]
    if dropped:
        logger.debug(
            "Dropping client-sent sampling params unsupported by %s upstream: %s",
            target_format,
            ", ".join(dropped),
        )
