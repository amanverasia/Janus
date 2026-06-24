from __future__ import annotations

from .openai_compat import OpenAICompatProvider


class OpenCodeFreeProvider(OpenAICompatProvider):
    name = "opencode_free"

    def __init__(self) -> None:
        super().__init__(base_url="https://opencode.ai/zen/v1", api_key=None)
