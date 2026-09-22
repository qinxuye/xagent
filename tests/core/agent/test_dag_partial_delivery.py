from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from xagent.core.agent import DAGPattern, ExecutionContext, PatternRuntime, PlanStep
from xagent.core.agent.pattern.dag import dag as dag_module

from .test_dag import FakeTool, SequenceLLM, build_plan, current_step_task


def call(name: str, **args: Any) -> dict[str, Any]:
    return {
        "tool_calls": [
            {
                "id": "call",
                "type": "function",
                "function": {
                    "name": name,
                    "arguments": json.dumps(args),
                },
            }
        ]
    }


class ReportTool(FakeTool):
    async def run_json_async(self, args: dict[str, Any]) -> Any:
        self.calls.append(args)
        return {"success": True, "markdown_link": "[report.csv](file:report-id)"}


def context() -> ExecutionContext:
    ctx = ExecutionContext(execution_id="dag-partial")
    ctx.add_user_message("Create and verify a report, then publish it.")
    return ctx


def failed_pattern() -> DAGPattern:
    pattern = DAGPattern(lambda **_: build_plan())
    pattern.plan = build_plan(
        PlanStep(
            id="bad", task="Planner fiction must not become a fact", status="failed"
        ),
        PlanStep(id="later", task="Publish", dependencies=["bad"]),
    )
    pattern.failed_step_evidence = {
        "bad": {"observations": ["Saved [report.csv](file:report-id)"]}
    }
    return pattern


@pytest.mark.asyncio
async def test_delivers_evidence_without_unlocking_failed_dependencies() -> None:
    plan = build_plan(
        PlanStep(id="done", task="Initial finding"),
        PlanStep(id="bad", task="Planner fiction", dependencies=["done"]),
        PlanStep(id="later", task="Publish", dependencies=["bad"]),
    )
    pattern = DAGPattern(lambda **_: plan, react_max_iterations=1, max_concurrency=1)
    tool = ReportTool()
    llm = SequenceLLM(
        [
            call("final_answer", answer="Initial finding", outcome="completed"),
            call("calculator", expression="2+2"),
            call(
                "final_answer",
                answer="Saved [report.csv](file:report-id); verification and publishing not done.",
                outcome="completed",
            ),
        ]
    )
    result = await pattern.run(context=context(), tools=[tool], llm=llm)

    assert result["success"] is True
    assert result["completion_outcome"] == "partial"
    assert result["termination_reason"] == "step_failed"
    assert result["failed_step_id"] == "bad"
    assert "file:report-id" in result["output"]
    assert "not a completed task" in result["output"]
    assert [s.status for s in plan.steps] == ["completed", "failed", "pending"]
    assert pattern.step_results == {"done": "Initial finding"}
    assert len(tool.calls) == 1
    assert llm.calls == 3
    delivery = llm.call_kwargs[-1]
    assert [t["function"]["name"] for t in delivery["tools"]] == ["final_answer"]
    assert delivery["max_tokens"] == 2048
    assert delivery["tool_choice"]["function"]["name"] == "final_answer"
    assert "file:report-id" in str(delivery["messages"])
    assert "Planner fiction" not in str(delivery["messages"])


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "response",
    [
        call("calculator", expression="9+9"),
        call("final_answer", answer=" "),
        {"content": "Do not stream this unaccepted answer"},
        {
            "tool_calls": call("final_answer", answer="bad")["tool_calls"]
            + call("calculator", expression="9+9")["tool_calls"]
        },
    ],
)
async def test_invalid_handoff_keeps_failure_without_more_work(response: Any) -> None:
    pattern = failed_pattern()
    tool = FakeTool()
    llm = SequenceLLM([response])
    runtime = PatternRuntime()
    result = await pattern.run(
        context=context(), tools=[tool], llm=llm, runtime=runtime
    )
    assert result["success"] is False
    assert result["failure_reason"] == "step_failed"
    assert "output" not in result
    assert llm.calls == 1
    assert not tool.calls
    assert runtime.last_checkpoint["label"] == "dag_failed"


@pytest.mark.asyncio
async def test_delivery_attempt_and_evidence_survive_restore() -> None:
    pattern = failed_pattern()
    await pattern.run(
        context=context(), tools=[], llm=SequenceLLM([{"content": "invalid"}])
    )
    restored = DAGPattern(lambda **_: build_plan())
    restored.load_state(pattern.get_state())
    llm = SequenceLLM([])
    result = await restored.run(context=context(), tools=[], llm=llm)
    assert result["success"] is False
    assert restored.failed_step_evidence == pattern.failed_step_evidence
    assert not llm.call_kwargs


@pytest.mark.asyncio
async def test_delivery_timeout_cancels_call_and_keeps_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(dag_module, "DAG_FAILURE_DELIVERY_TIMEOUT_SECONDS", 0.01)
    cancelled = asyncio.Event()

    class HangingLLM:
        async def chat(self, **kwargs: Any) -> Any:
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

    runtime = PatternRuntime()
    result = await failed_pattern().run(
        context=context(), tools=[], llm=HangingLLM(), runtime=runtime
    )
    assert result["failure_reason"] == "step_failed"
    assert cancelled.is_set()
    assert not runtime._active_llm_tasks


@pytest.mark.asyncio
async def test_cancellation_during_delivery_propagates() -> None:
    started = asyncio.Event()

    class HangingLLM:
        async def chat(self, **kwargs: Any) -> Any:
            started.set()
            await asyncio.Event().wait()

    runtime = PatternRuntime()
    task = asyncio.create_task(
        failed_pattern().run(
            context=context(), tools=[], llm=HangingLLM(), runtime=runtime
        )
    )
    await asyncio.wait_for(started.wait(), timeout=1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not runtime._active_llm_tasks


@pytest.mark.asyncio
async def test_stop_during_handoff_does_not_deliver() -> None:
    runtime = PatternRuntime()

    class StopLLM:
        async def chat(self, **kwargs: Any) -> Any:
            runtime.request_interrupt("User stopped")
            return call("final_answer", answer="Must not deliver", outcome="partial")

    result = await failed_pattern().run(
        context=context(), tools=[], llm=StopLLM(), runtime=runtime
    )
    assert result["failure_reason"] == "step_failed"
    assert "output" not in result


@pytest.mark.asyncio
async def test_no_extra_llm_call_when_failure_has_no_results() -> None:
    pattern = failed_pattern()
    pattern.failed_step_evidence = {}
    llm = SequenceLLM([])
    result = await pattern.run(context=context(), tools=[], llm=llm)
    assert result["success"] is False
    assert not llm.call_kwargs


@pytest.mark.asyncio
async def test_parallel_siblings_are_drained_before_handoff() -> None:
    running = asyncio.Event()
    cancelled = asyncio.Event()
    plan = build_plan(
        PlanStep(id="good", task="Already done"),
        PlanStep(id="bad", task="Fail", dependencies=["good"]),
        PlanStep(id="slow", task="Slow", dependencies=["good"]),
        PlanStep(id="later", task="Publish", dependencies=["bad"]),
    )
    pattern = DAGPattern(lambda **_: plan, max_concurrency=2)

    class LLM:
        async def chat(self, **kwargs: Any) -> Any:
            names = [t["function"]["name"] for t in kwargs["tools"]]
            if names == ["final_answer"]:
                assert cancelled.is_set()
                assert not pattern._live_step_tasks
                assert "provider-secret" not in str(kwargs["messages"])
                return call(
                    "final_answer",
                    answer="Initial result available; publishing not done.",
                    outcome="partial",
                )
            task = current_step_task(kwargs["messages"])
            if task == "Already done":
                return call(
                    "final_answer", answer="Initial result", outcome="completed"
                )
            if task == "Slow":
                running.set()
                try:
                    await asyncio.Event().wait()
                finally:
                    cancelled.set()
            assert task == "Fail"
            await running.wait()
            raise RuntimeError("provider-secret")

    result = await pattern.run(context=context(), tools=[], llm=LLM())
    assert result["completion_outcome"] == "partial"
    assert pattern.step_results == {"good": "Initial result"}
    assert plan.steps[-1].status == "pending"
    assert "provider-secret" not in result["output"]
