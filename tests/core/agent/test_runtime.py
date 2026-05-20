from __future__ import annotations

import asyncio
from typing import Any

import pytest

from xagent.core.agent import ExecutionContext, PatternRuntime
from xagent.core.agent.runtime import LLMCallInterrupted
from xagent.core.model.chat.types import ChunkType, StreamChunk


class SlowLLM:
    def __init__(self) -> None:
        self.started = asyncio.Event()

    async def chat(self, **_: Any) -> str:
        self.started.set()
        await asyncio.sleep(60)
        return "never"


class CancelledLLM:
    async def chat(self, **_: Any) -> str:
        raise asyncio.CancelledError


class StreamingLLM:
    async def stream_chat(self, **_: Any) -> Any:
        yield StreamChunk(type=ChunkType.TOKEN, delta="hello")
        yield StreamChunk(type=ChunkType.TOKEN, delta=" world")
        yield StreamChunk(type=ChunkType.END)


class ChatOnlyLLM:
    async def chat(self, **_: Any) -> str:
        return "complete answer"


class OutboundCollector:
    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    async def __call__(self, payload: dict[str, Any]) -> None:
        self.events.append(payload)


class CheckpointTracer:
    def __init__(self) -> None:
        self.checkpoints: list[dict[str, Any]] = []
        self.events: list[dict[str, Any]] = []

    async def checkpoint(self, **payload: Any) -> None:
        self.checkpoints.append(payload)

    async def trace_event(
        self,
        event_type: Any,
        *,
        task_id: str | None = None,
        step_id: str | None = None,
        data: dict[str, Any] | None = None,
    ) -> None:
        self.events.append(
            {
                "event_type": getattr(event_type, "value", str(event_type)),
                "task_id": task_id,
                "step_id": step_id,
                "data": data or {},
            }
        )


class TraceOnlyTracer:
    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    async def trace_event(
        self,
        event_type: Any,
        *,
        task_id: str | None = None,
        data: dict[str, Any] | None = None,
    ) -> None:
        self.events.append(
            {
                "event_type": getattr(event_type, "value", str(event_type)),
                "task_id": task_id,
                "data": data or {},
            }
        )


class FailingTraceOnlyTracer:
    async def trace_event(self, *_: Any, **__: Any) -> None:
        raise RuntimeError("trace failed")


class PatternWithState:
    status = "running"

    def get_state(self) -> dict[str, Any]:
        return {"step": 1}


@pytest.mark.asyncio
async def test_runtime_interrupt_converts_active_llm_cancel() -> None:
    runtime = PatternRuntime()
    llm = SlowLLM()
    task = asyncio.create_task(runtime.run_llm_call(llm))

    await llm.started.wait()
    runtime.request_interrupt("stop now")

    with pytest.raises(LLMCallInterrupted, match="stop now"):
        await task


@pytest.mark.asyncio
async def test_runtime_preserves_non_interrupt_cancelled_error() -> None:
    runtime = PatternRuntime()

    with pytest.raises(asyncio.CancelledError):
        await runtime.run_llm_call(CancelledLLM())


@pytest.mark.asyncio
async def test_runtime_stream_final_answer_emits_ui_events() -> None:
    outbound = OutboundCollector()
    runtime = PatternRuntime(execution_id="task-123", outbound_message_handler=outbound)

    result = await runtime.stream_final_answer(
        StreamingLLM(), messages=[{"role": "user", "content": "Say hi"}]
    )

    assert result == "hello world"
    assert [event["type"] for event in outbound.events] == [
        "final_answer_start",
        "final_answer_delta",
        "final_answer_delta",
        "final_answer_end",
    ]
    assert outbound.events[0]["task_id"] == "task-123"
    assert outbound.events[1]["delta"] == "hello"
    assert outbound.events[2]["delta"] == " world"
    assert outbound.events[3]["content"] == "hello world"
    assert len({event["message_id"] for event in outbound.events}) == 1


@pytest.mark.asyncio
async def test_runtime_stream_final_answer_falls_back_to_chat_without_events() -> None:
    outbound = OutboundCollector()
    runtime = PatternRuntime(outbound_message_handler=outbound)

    result = await runtime.stream_final_answer(ChatOnlyLLM(), messages=[])

    assert result == "complete answer"
    assert outbound.events == []


@pytest.mark.asyncio
async def test_runtime_checkpoint_prefers_checkpoint_api() -> None:
    tracer = CheckpointTracer()
    runtime = PatternRuntime(tracer=tracer, execution_id="exec-runtime")
    context = ExecutionContext(execution_id="exec-runtime")

    payload = await runtime.checkpoint(
        "before_llm",
        context=context,
        pattern=PatternWithState(),
        status="running",
    )

    assert payload["label"] == "before_llm"
    assert tracer.checkpoints[0]["execution_id"] == "exec-runtime"
    assert tracer.checkpoints[0]["pattern_state"] == {"step": 1}


@pytest.mark.asyncio
async def test_runtime_checkpoint_trace_event_fallback_is_task_scoped() -> None:
    tracer = TraceOnlyTracer()
    runtime = PatternRuntime(tracer=tracer, execution_id="exec-runtime")
    context = ExecutionContext(execution_id="exec-runtime")

    await runtime.checkpoint("fallback", context=context, pattern=PatternWithState())

    assert tracer.events[0]["event_type"] == "task_update_general"
    assert tracer.events[0]["task_id"] == "exec-runtime"
    assert tracer.events[0]["data"]["label"] == "fallback"


@pytest.mark.asyncio
async def test_runtime_trace_events_are_best_effort() -> None:
    runtime = PatternRuntime(
        tracer=FailingTraceOnlyTracer(), execution_id="exec-runtime"
    )

    await runtime.on_llm_start(context=ExecutionContext(), messages=[], tools=[])
