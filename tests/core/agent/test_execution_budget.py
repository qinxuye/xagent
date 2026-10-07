import asyncio
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from xagent.core.agent.budget import (
    ExecutionBudget,
    ExecutionBudgetPolicy,
    active_execution_budget,
)
from xagent.core.agent.context import ExecutionContext
from xagent.core.agent.runner import AgentRunner
from xagent.core.agent.runtime import (
    LLMCallInterrupted,
    PatternRuntime,
    ToolCallInterrupted,
)
from xagent.core.model.chat.token_context import (
    TokenContextManager,
    add_token_usage,
    token_usage_observer,
)


class MeteredLLM:
    def __init__(self, tokens=30):
        self.tokens = tokens
        self.calls = []

    async def chat(self, **kwargs):
        self.calls.append(kwargs)
        add_token_usage(
            input_tokens=self.tokens - 1, output_tokens=1, cached_input_tokens=10
        )
        return {"content": "result"}


class LoopPattern:
    status = "running"

    async def run(self, context, runtime, llm=None, **kwargs):
        self.runtime = runtime
        self.context = context
        while not await runtime.should_interrupt():
            await runtime.run_llm_call(
                llm, messages=[{"role": "user", "content": "work"}]
            )
            await runtime.checkpoint("after_call", context=context, pattern=self)
        return {"status": "interrupted", "success": False}


def runner(pattern, llm, policy):
    return AgentRunner(
        SimpleNamespace(patterns=[pattern], tools=[], llm=llm),
        workspace_enabled=False,
        budget_policy_provider=lambda: policy,
    )


@pytest.mark.asyncio
async def test_real_runner_stops_and_warns_once_without_extra_summary_call():
    llm = MeteredLLM()
    pattern = LoopPattern()
    result = await runner(
        pattern, llm, ExecutionBudgetPolicy(max_tokens=100, soft_limit_percent=50)
    ).run("work")
    assert len(llm.calls) == 4  # last admitted call overshoots; no fifth call
    assert "Execution token budget" not in str(llm.calls[0])
    assert "Execution token budget" in str(llm.calls[2])
    assert len(pattern.runtime.outbound_messages) == 1
    assert result["termination_reason"] == "token_budget"
    assert result["completion_outcome"] == "blocked"
    assert result["context"].execution_budget["used_tokens"] == 120
    assert pattern.runtime.last_checkpoint["context"]["execution_budget"]["closed"]
    assert active_execution_budget.get() is None
    assert token_usage_observer.get() is None


@pytest.mark.asyncio
async def test_unlimited_run_does_not_add_budget_only_tail_checkpoint():
    class RecordingPattern:
        async def run(self, context, runtime, llm, **kwargs):
            self.runtime = runtime
            await runtime.run_llm_call(llm, messages=[])
            context.add_assistant_message("recorded")
            await runtime.checkpoint("final", context=context, pattern=self)
            return {"success": True, "output": "recorded"}

    pattern = RecordingPattern()
    llm = MeteredLLM()
    result = await runner(pattern, llm, ExecutionBudgetPolicy()).run("work")

    assert result["success"]
    assert len(llm.calls) == 1
    assert result["context"].execution_budget is None
    assert pattern.runtime.last_checkpoint["label"] == "final"
    assert pattern.runtime.last_checkpoint["context"]["execution_budget"] is None
    assert not pattern.runtime.outbound_messages


@pytest.mark.asyncio
async def test_resume_cannot_reset_or_relax_budget_but_new_completed_turn_can():
    old_context = ExecutionContext()
    old_context.add_user_message("work", metadata={"turn_id": "one"})
    old_context.execution_budget = ExecutionBudget(
        policy=ExecutionBudgetPolicy(max_tokens=100), used_tokens=100, turn_id="one"
    ).model_dump()
    llm = MeteredLLM()
    pattern = LoopPattern()
    first = await runner(pattern, llm, ExecutionBudgetPolicy(max_tokens=1000)).run(
        None, checkpoint={"context": old_context.to_dict()}
    )
    assert not llm.calls
    assert first["context"].execution_budget["policy"]["max_tokens"] == 100
    completed_context = first["context"]
    completed_context.add_user_message("next", metadata={"turn_id": "two"})
    second = await runner(LoopPattern(), llm, ExecutionBudgetPolicy(max_tokens=60)).run(
        None, checkpoint={"context": completed_context.to_dict()}
    )
    assert len(llm.calls) == 2
    assert second["context"].execution_budget["used_tokens"] == 60


@pytest.mark.asyncio
async def test_user_metadata_cannot_supply_budget_or_reset_consumption():
    pattern = LoopPattern()
    result = await runner(
        pattern, MeteredLLM(), ExecutionBudgetPolicy(max_tokens=30)
    ).run(
        "work",
        metadata={
            "execution_budget": {"used_tokens": 0, "policy": {"max_tokens": 99999}},
            "request_context": {"execution_budget": None},
        },
    )
    assert result["context"].execution_budget["used_tokens"] == 30


@pytest.mark.asyncio
async def test_existing_quota_gate_takes_precedence():
    llm = MeteredLLM()
    result = await runner(LoopPattern(), llm, ExecutionBudgetPolicy(max_tokens=30)).run(
        "work", interrupt_checker=lambda: "quota unavailable"
    )
    assert result["status"] == "interrupted"
    assert "termination_reason" not in result
    assert not llm.calls


@pytest.mark.asyncio
async def test_observer_counts_nested_contexts_and_cached_input_once():
    budget = ExecutionBudget(policy=ExecutionBudgetPolicy(max_tokens=100))
    token = token_usage_observer.set(budget.record_usage)
    try:
        with TokenContextManager():
            add_token_usage(input_tokens=30, output_tokens=5, cached_input_tokens=20)
            with TokenContextManager():
                add_token_usage(input_tokens=40, output_tokens=10)
        assert budget.used_tokens == 85
    finally:
        token_usage_observer.reset(token)


@pytest.mark.asyncio
async def test_exhausted_budget_blocks_llm_and_tool_before_invocation():
    budget = ExecutionBudget(policy=ExecutionBudgetPolicy(max_tokens=1), used_tokens=1)
    token = active_execution_budget.set(budget)
    try:
        llm = MeteredLLM()
        with pytest.raises(LLMCallInterrupted):
            await PatternRuntime().run_llm_call(llm, messages=[])
        with pytest.raises(ToolCallInterrupted):
            await PatternRuntime().run_tool_call(
                lambda: pytest.fail("tool must not run")
            )
        assert not llm.calls
    finally:
        active_execution_budget.reset(token)


@pytest.mark.parametrize(
    "values",
    [
        {"max_tokens": 0},
        {"max_tokens": -1},
        {"max_tokens": True},
        {"soft_limit_percent": 100},
        {"soft_limit_percent": 0},
    ],
)
def test_invalid_policies_are_rejected(values):
    with pytest.raises(ValidationError):
        ExecutionBudgetPolicy(**values)


def test_context_snapshot_does_not_share_budget_containers():
    context = ExecutionContext(
        execution_budget=ExecutionBudget(
            policy=ExecutionBudgetPolicy(max_tokens=100)
        ).model_dump()
    )
    payload = context.to_dict()
    context.execution_budget["used_tokens"] = 50
    assert ExecutionContext.from_dict(payload).execution_budget["used_tokens"] == 0


@pytest.mark.asyncio
async def test_nested_runner_cannot_get_a_separate_allowance():
    llm = MeteredLLM(tokens=30)

    class ParentPattern:
        async def run(self, context, runtime, **kwargs):
            await runtime.run_llm_call(llm, messages=[])
            child = runner(LoopPattern(), llm, ExecutionBudgetPolicy(max_tokens=99999))
            child_result = await child.run("child work")
            assert child_result["termination_reason"] == "token_budget"
            assert not active_execution_budget.get().closed
            with pytest.raises(LLMCallInterrupted):
                await runtime.run_llm_call(llm, messages=[])
            return {"success": False, "status": "interrupted"}

    result = await runner(
        ParentPattern(), llm, ExecutionBudgetPolicy(max_tokens=60)
    ).run("parent work")
    assert len(llm.calls) == 2
    assert result["context"].execution_budget["used_tokens"] == 60


@pytest.mark.asyncio
async def test_parallel_calls_share_usage_and_next_batch_is_stopped():
    llm = MeteredLLM(tokens=30)

    class ParallelPattern:
        async def run(self, context, runtime, **kwargs):
            await asyncio.gather(
                *(runtime.run_llm_call(llm, messages=[]) for _ in range(3))
            )
            with pytest.raises(LLMCallInterrupted):
                await runtime.run_llm_call(llm, messages=[])
            return {"success": False, "status": "interrupted"}

    result = await runner(
        ParallelPattern(), llm, ExecutionBudgetPolicy(max_tokens=60)
    ).run("parallel")
    assert len(llm.calls) == 3  # all three were admitted before usage was reported
    assert result["context"].execution_budget["used_tokens"] == 90


@pytest.mark.asyncio
async def test_budget_blocks_native_stream_before_provider_is_called():
    class StreamLLM:
        async def stream_chat(self, **kwargs):
            pytest.fail("exhausted budget must not call streaming provider")
            yield None

    token = active_execution_budget.set(
        ExecutionBudget(policy=ExecutionBudgetPolicy(max_tokens=1), used_tokens=1)
    )
    try:
        with pytest.raises(LLMCallInterrupted):
            await PatternRuntime().run_streaming_llm_call(StreamLLM(), messages=[])
    finally:
        active_execution_budget.reset(token)


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["react", "dag"])
async def test_real_patterns_stop_before_executing_over_budget_tool(mode):
    from tests.core.agent.test_react import FakeTool
    from xagent.core.agent import DAGPattern, ExecutionPlan, PlanStep, ReActPattern

    class WorkingLLM(MeteredLLM):
        async def chat(self, **kwargs):
            await super().chat(**kwargs)
            return {
                "tool_calls": [
                    {
                        "id": str(len(self.calls)),
                        "name": "calculator",
                        "args": {"expression": "1 + 1"},
                    }
                ]
            }

    llm = WorkingLLM(tokens=30)
    tool = FakeTool()
    if mode == "react":
        pattern = ReActPattern(max_iterations=10)
    else:
        pattern = DAGPattern(
            plan_generator=lambda **kwargs: ExecutionPlan(
                steps=[
                    PlanStep(id="first", task="Calculate", tool_names=["calculator"])
                ]
            ),
            max_concurrency=1,
        )
    agent_runner = runner(
        pattern, llm, ExecutionBudgetPolicy(max_tokens=60, soft_limit_percent=50)
    )
    agent_runner.agent.tools = [tool]
    result = await agent_runner.run(
        "Use calculator repeatedly", metadata={"output_language": "Chinese"}
    )
    assert result.get("termination_reason") == "token_budget", result
    assert len(llm.calls) == 2
    assert len(tool.calls) == 1
    assert result["context"].execution_budget["used_tokens"] == 60
    assert "本次执行" in result["output"]


@pytest.mark.asyncio
async def test_budget_stop_hands_over_registered_files_only():
    from unittest.mock import AsyncMock

    agent_runner = runner(
        LoopPattern(), MeteredLLM(), ExecutionBudgetPolicy(max_tokens=100)
    )
    runtime = PatternRuntime(budget_owner=True)
    context = ExecutionContext()
    workspace = SimpleNamespace(
        get_output_files=lambda: [
            {"file_id": "saved-file", "filename": "result.csv"},
            {"file_id": None, "filename": "unregistered.csv"},
        ]
    )
    runtime.checkpoint = AsyncMock()
    runtime.checkpoint_context_tail = AsyncMock()
    result = await agent_runner._finish_budget_stop(
        context, runtime, LoopPattern(), workspace
    )
    assert "[result.csv](file:saved-file)" in result["output"]
    assert "unregistered.csv" not in result["output"]
    assert result["completion_outcome"] == "partial"


@pytest.mark.asyncio
async def test_budget_stop_includes_real_workspace_root_but_not_inputs_or_temp(
    tmp_path,
):
    from unittest.mock import AsyncMock

    from xagent.core.workspace import TaskWorkspace

    workspace = TaskWorkspace("budget-stop", base_dir=str(tmp_path))
    for directory, name in (
        (workspace.workspace_dir, "root-result.csv"),
        (workspace.output_dir, "output-result.csv"),
        (workspace.input_dir, "source.csv"),
        (workspace.temp_dir, "scratch.csv"),
    ):
        path = directory / name
        path.write_text("value\n1\n")
        workspace.register_file(str(path))
    (workspace.workspace_dir / "unregistered.csv").write_text("value\n2\n")
    agent_runner = runner(
        LoopPattern(), MeteredLLM(), ExecutionBudgetPolicy(max_tokens=100)
    )
    runtime = PatternRuntime(budget_owner=True)
    runtime.checkpoint = AsyncMock()
    runtime.checkpoint_context_tail = AsyncMock()

    result = await agent_runner._finish_budget_stop(
        ExecutionContext(), runtime, LoopPattern(), workspace
    )

    for name in ("root-result.csv", "output-result.csv"):
        assert f"[{name}](file:" in result["output"]
    for name in ("source.csv", "scratch.csv", "unregistered.csv"):
        assert name not in result["output"]
    assert result["completion_outcome"] == "partial"


@pytest.mark.asyncio
async def test_final_answer_already_paid_for_is_not_discarded_at_limit():
    from xagent.core.agent import ReActPattern

    class AnswerLLM(MeteredLLM):
        async def chat(self, **kwargs):
            await super().chat(**kwargs)
            return {
                "tool_calls": [
                    {
                        "id": "final",
                        "name": "final_answer",
                        "args": {"answer": "The answer is 42.", "outcome": "completed"},
                    }
                ]
            }

    llm = AnswerLLM(tokens=30)
    result = await runner(
        ReActPattern(), llm, ExecutionBudgetPolicy(max_tokens=20)
    ).run("Answer directly")
    assert result["success"]
    assert result.get("termination_reason") != "token_budget"
    assert "42" in str(result)
    assert len(llm.calls) == 1


@pytest.mark.asyncio
async def test_policy_failure_stops_before_any_calls_and_cleans_context():
    llm = MeteredLLM()
    agent_runner = runner(LoopPattern(), llm, ExecutionBudgetPolicy())

    async def unavailable():
        raise RuntimeError("policy unavailable")

    agent_runner.budget_policy_provider = unavailable
    with pytest.raises(RuntimeError, match="policy unavailable"):
        await agent_runner.run("work")
    assert not llm.calls
    assert active_execution_budget.get() is None
    assert token_usage_observer.get() is None


@pytest.mark.asyncio
async def test_pause_and_new_runner_resume_keep_recorded_consumption():
    llm = MeteredLLM(tokens=30)

    class PausePattern:
        async def run(self, context, runtime, **kwargs):
            self.runtime = runtime
            await runtime.run_llm_call(llm, messages=[])
            await runtime.checkpoint(
                "paused", context=context, pattern=self, status="interrupted"
            )
            return {"success": False, "status": "interrupted"}

    pattern = PausePattern()
    await runner(pattern, llm, ExecutionBudgetPolicy(max_tokens=60)).run("work")
    saved = pattern.runtime.last_checkpoint
    assert saved["context"]["execution_budget"]["used_tokens"] == 30
    result = await runner(LoopPattern(), llm, ExecutionBudgetPolicy(max_tokens=60)).run(
        None, checkpoint=saved
    )
    assert len(llm.calls) == 2
    assert result["context"].execution_budget["used_tokens"] == 60


@pytest.mark.asyncio
async def test_new_user_turn_after_failed_checkpoint_gets_fresh_budget():
    context = ExecutionContext()
    context.add_user_message("new request", metadata={"turn_id": "new-turn"})
    context.execution_budget = ExecutionBudget(
        policy=ExecutionBudgetPolicy(max_tokens=30), used_tokens=30, turn_id="old-turn"
    ).model_dump()
    llm = MeteredLLM()
    result = await runner(LoopPattern(), llm, ExecutionBudgetPolicy(max_tokens=30)).run(
        None, checkpoint={"context": context.to_dict(), "status": "failed"}
    )
    assert len(llm.calls) == 1
    assert result["context"].execution_budget["turn_id"] == "new-turn"
