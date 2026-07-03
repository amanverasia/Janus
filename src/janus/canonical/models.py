from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field


class Role(StrEnum):
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


class TextPart(BaseModel):
    type: Literal["text"] = "text"
    text: str


class ImageSource(BaseModel):
    type: Literal["url", "base64"]
    url: str | None = None
    media_type: str | None = None
    data: str | None = None


class ImagePart(BaseModel):
    type: Literal["image"] = "image"
    source: ImageSource


class ToolUse(BaseModel):
    type: Literal["tool_use"] = "tool_use"
    id: str
    name: str
    input: dict[str, Any]


class ToolResult(BaseModel):
    type: Literal["tool_result"] = "tool_result"
    tool_use_id: str
    content: str


ContentPart = Annotated[
    TextPart | ImagePart | ToolUse | ToolResult,
    Field(discriminator="type"),
]


class Message(BaseModel):
    role: Role
    content: str | list[ContentPart]
    reasoning_content: str | None = None


class SystemBlock(BaseModel):
    type: Literal["text"] = "text"
    text: str


class ToolFunction(BaseModel):
    name: str
    description: str | None = None
    parameters: dict[str, Any]


class Tool(BaseModel):
    type: Literal["function"] = "function"
    function: ToolFunction


class ToolChoiceAuto(BaseModel):
    type: Literal["auto"] = "auto"


class ToolChoiceNone(BaseModel):
    type: Literal["none"] = "none"


class ToolChoiceRequired(BaseModel):
    type: Literal["required"] = "required"


class ToolChoiceSpecific(BaseModel):
    type: Literal["specific"] = "specific"
    name: str


ToolChoiceType = Annotated[
    ToolChoiceAuto | ToolChoiceNone | ToolChoiceRequired | ToolChoiceSpecific,
    Field(discriminator="type"),
]


class Usage(BaseModel):
    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0


class CanonicalRequest(BaseModel):
    model: str
    system: list[SystemBlock] = Field(default_factory=list)
    messages: list[Message]
    tools: list[Tool] = Field(default_factory=list)
    tool_choice: ToolChoiceType | None = None
    max_tokens: int | None = None
    temperature: float | None = None
    top_p: float | None = None
    stop: list[str] | None = None
    stream: bool = False
    thinking: dict[str, str] | None = None
    reasoning_effort: str | None = None


class CanonicalResponse(BaseModel):
    model: str
    role: Literal["assistant"] = "assistant"
    content: list[ContentPart]
    stop_reason: str | None = None
    usage: Usage = Field(default_factory=Usage)
    reasoning_content: str | None = None
