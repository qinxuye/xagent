from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ...model.chat.types import ChunkType
from ..runtime import PatternRuntime


@dataclass(frozen=True)
class _StringField:
    value: str
    complete: bool


class FinalAnswerStreamEmitter:
    """Lazy final-answer UI stream emitter."""

    def __init__(self, runtime: PatternRuntime, *, enabled: bool = True) -> None:
        self.runtime = runtime
        self.enabled = enabled
        self.message_id: str | None = None
        self._emitted_chars = 0

    @property
    def started(self) -> bool:
        return self.message_id is not None

    async def emit_delta(self, delta: str) -> None:
        if not self.enabled or not delta:
            return
        if self.message_id is None:
            self.message_id = await self.runtime.start_final_answer_stream()
            if self.message_id is None:
                return
        self._emitted_chars += len(delta)
        await self.runtime.emit_final_answer_delta(self.message_id, delta)

    async def emit_prefix(self, content: str) -> None:
        if len(content) <= self._emitted_chars:
            return
        await self.emit_delta(content[self._emitted_chars :])

    async def finish(self, content: str) -> None:
        if self.message_id is not None:
            await self.runtime.end_final_answer_stream(self.message_id, content)


class ToolCallStringFieldStreamer:
    """Streams a string field from accumulated streamed tool-call arguments."""

    def __init__(
        self,
        *,
        runtime: PatternRuntime,
        tool_name: str,
        field_name: str,
        guard_field: str | None = None,
        guard_value: str | None = None,
        emitter: FinalAnswerStreamEmitter | None = None,
        enabled: bool = True,
    ) -> None:
        self.tool_name = tool_name
        self.field_name = field_name
        self.guard_field = guard_field
        self.guard_value = guard_value
        self.emitter = emitter or FinalAnswerStreamEmitter(runtime, enabled=enabled)
        self._guard_confirmed = guard_field is None
        self._disabled = not enabled

    @property
    def started(self) -> bool:
        return self.emitter.started

    async def handle_chunk(self, chunk: Any) -> None:
        if self._disabled or not _is_tool_call_chunk(chunk):
            return

        arguments = _tool_call_arguments(chunk, self.tool_name)
        if arguments is None:
            return

        wanted = {self.field_name}
        if self.guard_field:
            wanted.add(self.guard_field)
        fields = _JsonStringFieldReader(arguments).read(wanted)

        if self.guard_field:
            guard = fields.get(self.guard_field)
            if guard and guard.complete:
                if guard.value != self.guard_value:
                    self._disabled = True
                    return
                self._guard_confirmed = True

        if not self._guard_confirmed:
            return

        field = fields.get(self.field_name)
        if field is not None:
            await self.emitter.emit_prefix(field.value)

    async def finish(self, final_content: str) -> None:
        await self.emitter.finish(final_content)


class ReActFinalAnswerStreamer:
    """Streams ReAct final answers from final_answer control-tool args."""

    def __init__(self, runtime: PatternRuntime, *, enabled: bool = True) -> None:
        self.emitter = FinalAnswerStreamEmitter(runtime, enabled=enabled)
        self._tool_answer_streamer = ToolCallStringFieldStreamer(
            runtime=runtime,
            tool_name="final_answer",
            field_name="answer",
            emitter=self.emitter,
            enabled=enabled,
        )

    @property
    def started(self) -> bool:
        return self.emitter.started

    async def handle_chunk(self, chunk: Any) -> None:
        await self._tool_answer_streamer.handle_chunk(chunk)

    async def finish(self, final_content: str) -> None:
        await self.emitter.finish(final_content)


def _is_tool_call_chunk(chunk: Any) -> bool:
    chunk_type = getattr(chunk, "type", None)
    is_tool_call = (
        callable(getattr(chunk, "is_tool_call", None)) and chunk.is_tool_call()
    )
    return chunk_type == ChunkType.TOOL_CALL or is_tool_call


def _chunk_text_delta(chunk: Any) -> str:
    chunk_type = getattr(chunk, "type", None)
    is_token = callable(getattr(chunk, "is_token", None)) and chunk.is_token()
    if chunk_type != ChunkType.TOKEN and not is_token:
        return ""
    return str(getattr(chunk, "delta", "") or getattr(chunk, "content", "") or "")


def _tool_call_arguments(chunk: Any, tool_name: str) -> str | None:
    for tool_call in list(getattr(chunk, "tool_calls", None) or []):
        function_payload = _function_payload(tool_call)
        if function_payload.get("name") != tool_name:
            continue
        arguments = function_payload.get("arguments")
        return arguments if isinstance(arguments, str) else None
    return None


def _function_payload(tool_call: Any) -> dict[str, Any]:
    if isinstance(tool_call, dict):
        payload = tool_call.get("function")
        return payload if isinstance(payload, dict) else {}
    function_payload = getattr(tool_call, "function", None)
    if function_payload is None:
        return {}
    return {
        "name": getattr(function_payload, "name", None),
        "arguments": getattr(function_payload, "arguments", None),
    }


class _JsonStringFieldReader:
    """Small incremental reader for string fields in a top-level JSON object."""

    def __init__(self, source: str) -> None:
        self.source = source
        self.length = len(source)

    def read(self, wanted: set[str]) -> dict[str, _StringField]:
        fields: dict[str, _StringField] = {}
        index = self._skip_ws(0)
        if index >= self.length or self.source[index] != "{":
            return fields
        index += 1

        while index < self.length:
            index = self._skip_ws_and_commas(index)
            key = self._parse_complete_string(index)
            if key is None:
                return fields
            key_value, index = key
            index = self._skip_ws(index)
            if index >= self.length or self.source[index] != ":":
                return fields
            index = self._skip_ws(index + 1)

            if (
                key_value in wanted
                and index < self.length
                and self.source[index] == '"'
            ):
                value, complete, index = self._parse_string_prefix(index)
                fields[key_value] = _StringField(value=value, complete=complete)
                if not complete:
                    return fields
            else:
                index = self._skip_value(index)
        return fields

    def _skip_ws(self, index: int) -> int:
        while index < self.length and self.source[index].isspace():
            index += 1
        return index

    def _skip_ws_and_commas(self, index: int) -> int:
        while index < self.length and (
            self.source[index].isspace() or self.source[index] == ","
        ):
            index += 1
        return index

    def _parse_complete_string(self, index: int) -> tuple[str, int] | None:
        if index >= self.length or self.source[index] != '"':
            return None
        value, complete, end = self._parse_string_prefix(index)
        return (value, end) if complete else None

    def _parse_string_prefix(self, index: int) -> tuple[str, bool, int]:
        index += 1
        chars: list[str] = []
        while index < self.length:
            char = self.source[index]
            if char == '"':
                return "".join(chars), True, index + 1
            if char == "\\":
                escaped, index, complete = self._parse_escape(index + 1)
                if not complete:
                    return "".join(chars), False, index
                chars.append(escaped)
                continue
            chars.append(char)
            index += 1
        return "".join(chars), False, index

    def _parse_escape(self, index: int) -> tuple[str, int, bool]:
        if index >= self.length:
            return "", index, False
        char = self.source[index]
        if char == "u":
            digits = self.source[index + 1 : index + 5]
            if len(digits) < 4 or any(
                digit not in "0123456789abcdefABCDEF" for digit in digits
            ):
                return "", index, False
            return chr(int(digits, 16)), index + 5, True
        mapping = {
            '"': '"',
            "\\": "\\",
            "/": "/",
            "b": "\b",
            "f": "\f",
            "n": "\n",
            "r": "\r",
            "t": "\t",
        }
        return mapping.get(char, char), index + 1, True

    def _skip_value(self, index: int) -> int:
        depth = 0
        while index < self.length:
            char = self.source[index]
            if char == '"':
                parsed = self._parse_complete_string(index)
                if parsed is None:
                    return self.length
                _, index = parsed
                continue
            if char in "[{":
                depth += 1
            elif char in "]}":
                if depth == 0:
                    return index
                depth -= 1
            elif char == "," and depth == 0:
                return index + 1
            index += 1
        return index
