from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, Field

from .models import Usage


class MessageStart(BaseModel):
    type: Literal["message_start"] = "message_start"
    model: str


class TextBlockStart(BaseModel):
    type: Literal["text_block_start"] = "text_block_start"
    index: int


class ToolUseBlockStart(BaseModel):
    type: Literal["tool_use_block_start"] = "tool_use_block_start"
    index: int
    id: str
    name: str


class TextDelta(BaseModel):
    type: Literal["text_delta"] = "text_delta"
    index: int
    text: str


class InputJsonDelta(BaseModel):
    type: Literal["input_json_delta"] = "input_json_delta"
    index: int
    partial_json: str


class BlockStop(BaseModel):
    type: Literal["block_stop"] = "block_stop"
    index: int


class MessageDelta(BaseModel):
    type: Literal["message_delta"] = "message_delta"
    stop_reason: str | None = None
    usage: Usage | None = None


class MessageStop(BaseModel):
    type: Literal["message_stop"] = "message_stop"


CanonicalEvent = Annotated[
    MessageStart
    | TextBlockStart
    | ToolUseBlockStart
    | TextDelta
    | InputJsonDelta
    | BlockStop
    | MessageDelta
    | MessageStop,
    Field(discriminator="type"),
]
