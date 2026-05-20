from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ....model.chat.types import ChunkType
from ...runtime import PatternRuntime


@dataclass(frozen=True)
class _StringField:
    value: str
    complete: bool


class AutoFinalAnswerStreamer:
    """Streams the answer field from Auto's decision tool arguments.

    The stream is best-effort. It only starts after the same streamed tool call has
    confirmed ``action == "final_answer"``. If the model chooses another action or
    the provider does not stream tool-call arguments incrementally, the normal
    final ai_message/task_completion path remains authoritative.
    """

    def __init__(
        self,
        *,
        runtime: PatternRuntime,
        tool_name: str,
        final_action: str,
        enabled: bool = True,
    ) -> None:
        self.runtime = runtime
        self.tool_name = tool_name
        self.final_action = final_action
        self.message_id: str | None = None
        self._action_confirmed = False
        self._disabled = not enabled
        self._emitted_chars = 0

    @property
    def started(self) -> bool:
        return self.message_id is not None

    async def handle_chunk(self, chunk: Any) -> None:
        if self._disabled or not self._is_tool_call_chunk(chunk):
            return

        arguments = self._decision_arguments(chunk)
        if arguments is None:
            return

        fields = _JsonStringFieldReader(arguments).read({"action", "answer"})
        action = fields.get("action")
        if action and action.complete:
            if action.value != self.final_action:
                self._disabled = True
                return
            self._action_confirmed = True

        if not self._action_confirmed:
            return

        answer = fields.get("answer")
        if answer is None:
            return
        await self._emit_answer_prefix(answer.value)

    async def finish(self, final_answer: str) -> None:
        if self.message_id is not None:
            await self.runtime.end_final_answer_stream(self.message_id, final_answer)

    def _is_tool_call_chunk(self, chunk: Any) -> bool:
        chunk_type = getattr(chunk, "type", None)
        is_tool_call = (
            callable(getattr(chunk, "is_tool_call", None)) and chunk.is_tool_call()
        )
        return chunk_type == ChunkType.TOOL_CALL or is_tool_call

    def _decision_arguments(self, chunk: Any) -> str | None:
        for tool_call in list(getattr(chunk, "tool_calls", None) or []):
            function_payload = self._function_payload(tool_call)
            if function_payload.get("name") != self.tool_name:
                continue
            arguments = function_payload.get("arguments")
            return arguments if isinstance(arguments, str) else None
        return None

    def _function_payload(self, tool_call: Any) -> dict[str, Any]:
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

    async def _emit_answer_prefix(self, answer: str) -> None:
        if len(answer) <= self._emitted_chars:
            return
        if self.message_id is None:
            self.message_id = await self.runtime.start_final_answer_stream()
            if self.message_id is None:
                return
        delta = answer[self._emitted_chars :]
        self._emitted_chars = len(answer)
        await self.runtime.emit_final_answer_delta(self.message_id, delta)


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
