from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from xagent.core.agent import ExecutionContext
from xagent.core.computer.environment import ComputerEnvironment
from xagent.core.computer.schema import (
    COMPUTER_FRAME_ID_METADATA_KEY,
    COMPUTER_SESSION_ID_METADATA_KEY,
    ComputerAction,
    ComputerActionBatch,
    ComputerActionType,
    ComputerElement,
    ComputerElementSource,
    ComputerElementSurface,
    ComputerEnvironmentType,
    ComputerObservation,
    ComputerPerceptionMode,
    ComputerTarget,
    NormalizedPoint,
    NormalizedRect,
    Viewport,
)
from xagent.core.context_ref import CONTEXT_REFS_KEY, ContextReference
from xagent.core.tools.adapters.vibe.computer import ComputerTool, ComputerToolArgs


def make_observation(session_id: str, index: int) -> ComputerObservation:
    frame_id = f"frame-{index}"
    return ComputerObservation(
        session_id=session_id,
        frame_id=frame_id,
        environment=ComputerEnvironmentType.BROWSER,
        viewport=Viewport(width=1280, height=720),
        screenshot=ContextReference(
            file_ref={
                "file_id": f"image-{index}",
                "filename": f"{frame_id}.png",
                "mime_type": "image/png",
                "internal": True,
            },
            metadata={
                COMPUTER_SESSION_ID_METADATA_KEY: session_id,
                COMPUTER_FRAME_ID_METADATA_KEY: frame_id,
            },
        ),
        active_url="about:blank",
    )


class FakeComputerEnvironment(ComputerEnvironment):
    def __init__(self, session_id: str) -> None:
        super().__init__(session_id)
        self.observe_count = 0
        self.executed: list[ComputerActionBatch] = []
        self.close_count = 0

    async def _observe(self) -> ComputerObservation:
        self.observe_count += 1
        return make_observation(self.session_id, self.observe_count)

    async def _execute(self, batch: ComputerActionBatch) -> ComputerObservation:
        self.executed.append(batch)
        self.observe_count += 1
        return make_observation(self.session_id, self.observe_count)

    async def _close(self) -> None:
        self.close_count += 1


class EnvironmentFactory:
    def __init__(self) -> None:
        self.environments: list[FakeComputerEnvironment] = []
        self.calls: list[dict[str, Any]] = []

    def __call__(self, **kwargs: Any) -> FakeComputerEnvironment:
        self.calls.append(kwargs)
        environment = FakeComputerEnvironment(kwargs["session_id"])
        self.environments.append(environment)
        return environment


class RestrictedComputerEnvironment(FakeComputerEnvironment):
    async def _observe(self) -> ComputerObservation:
        observation = await super()._observe()
        return observation.model_copy(
            update={
                "metadata": {
                    "supported_actions": ["screenshot", "click"],
                    "unsupported_actions": {
                        "type": "same_pid_keyboard_ambiguity",
                    },
                }
            }
        )


class RestrictedEnvironmentFactory(EnvironmentFactory):
    def __call__(self, **kwargs: Any) -> RestrictedComputerEnvironment:
        self.calls.append(kwargs)
        environment = RestrictedComputerEnvironment(kwargs["session_id"])
        self.environments.append(environment)
        return environment


class StaleScreenshotEnvironment(FakeComputerEnvironment):
    async def _observe(self) -> ComputerObservation:
        observation = await super()._observe()
        return observation.model_copy(update={"metadata": {"screenshot_fresh": False}})


class StaleScreenshotEnvironmentFactory(EnvironmentFactory):
    def __call__(self, **kwargs: Any) -> StaleScreenshotEnvironment:
        self.calls.append(kwargs)
        environment = StaleScreenshotEnvironment(kwargs["session_id"])
        self.environments.append(environment)
        return environment


class ArtifactWorkspace:
    def __init__(self, root: Path) -> None:
        self.workspace_dir = root
        self.output_dir = root / "output"
        self.output_dir.mkdir(parents=True)
        self.source = root / "frame.png"
        self.source.write_bytes(b"png-bytes")
        self.registered: dict[str, str] = {}

    def resolve_file_id(self, _file_id: str) -> Path:
        return self.source

    def get_file_id_from_path(self, file_path: str) -> str | None:
        return self.registered.get(str(Path(file_path).resolve()))

    def register_file(self, file_path: str) -> str:
        resolved = str(Path(file_path).resolve())
        file_id = f"public-{len(self.registered) + 1}"
        self.registered[resolved] = file_id
        return file_id


@pytest.mark.parametrize(
    ("mode", "description_fragment"),
    [
        (ComputerPerceptionMode.AUTO, "Never invent an element_id"),
        (
            ComputerPerceptionMode.VISION,
            "Semantic elements are intentionally not exposed",
        ),
        (ComputerPerceptionMode.SEMANTIC, "Prefer an exact element_id"),
    ],
)
def test_computer_tool_describes_explicit_perception_mode(
    mode: ComputerPerceptionMode,
    description_fragment: str,
) -> None:
    tool = ComputerTool(perception_mode=mode)

    assert f"Perception mode is {mode.value}" in tool.description
    assert description_fragment in tool.description


def test_computer_tool_normalizes_bare_element_id_target() -> None:
    parsed = ComputerToolArgs.model_validate(
        {
            "expected_frame_id": "frame-1",
            "action": "click",
            "target": "snapshot-1:4",
        }
    )

    assert parsed.target is not None
    assert parsed.target.element_id == "snapshot-1:4"
    assert parsed.target.point is None


@pytest.mark.parametrize(
    ("serialized_target", "element_id", "point"),
    [
        ('{"element_id": "snapshot-1:4"}', "snapshot-1:4", None),
        ('{"point": {"x": 0.25, "y": 0.75}}', None, (0.25, 0.75)),
    ],
)
def test_computer_tool_normalizes_json_encoded_target(
    serialized_target: str,
    element_id: str | None,
    point: tuple[float, float] | None,
) -> None:
    parsed = ComputerToolArgs.model_validate(
        {
            "expected_frame_id": "frame-1",
            "action": "click",
            "target": serialized_target,
        }
    )

    assert parsed.target is not None
    assert parsed.target.element_id == element_id
    assert (
        None
        if parsed.target.point is None
        else (parsed.target.point.x, parsed.target.point.y)
    ) == point


@pytest.mark.asyncio
async def test_computer_tool_requires_initial_observation_then_expected_frame() -> None:
    factory = EnvironmentFactory()
    tool = ComputerTool(
        task_id="task-1",
        workspace=object(),  # type: ignore[arg-type]
        environment_factory=factory,
    )

    initial = await tool.run_json_async({})

    assert initial["success"] is True
    assert initial["session_id"] == "task-1"
    assert initial["frame_id"] == "frame-1"
    assert initial[CONTEXT_REFS_KEY][0]["file_ref"]["file_id"] == "image-1"
    assert initial[CONTEXT_REFS_KEY][0]["file_ref"]["internal"] is True
    assert "file_ref" not in initial

    missing_frame = await tool.run_json_async(
        {
            "action": "click",
            "target": {"point": {"x": 0.5, "y": 0.5}},
        }
    )
    assert missing_frame["success"] is False
    assert "expected_frame_id" in missing_frame["error"]

    acted = await tool.run_json_async(
        {
            "expected_frame_id": "frame-1",
            "action": "click",
            "target": {"point": {"x": 0.5, "y": 0.5}},
        }
    )

    assert acted["success"] is True
    assert acted["frame_id"] == "frame-2"
    assert factory.environments[0].executed[0].expected_frame_id == "frame-1"


@pytest.mark.asyncio
async def test_computer_tool_stops_repeated_clicks_in_same_region() -> None:
    factory = EnvironmentFactory()
    tool = ComputerTool(
        task_id="task-1",
        workspace=object(),  # type: ignore[arg-type]
        environment_factory=factory,
    )
    current = await tool.run_json_async({})

    for x, y in ((0.75, 0.08), (0.76, 0.11), (0.755, 0.09)):
        current = await tool.run_json_async(
            {
                "expected_frame_id": current["frame_id"],
                "action": ComputerActionType.CLICK.value,
                "target": {"point": {"x": x, "y": y}},
            }
        )
        assert current["success"] is True

    refused = await tool.run_json_async(
        {
            "expected_frame_id": current["frame_id"],
            "action": ComputerActionType.CLICK.value,
            "target": {"point": {"x": 0.759, "y": 0.108}},
        }
    )

    assert refused["success"] is False
    assert refused["frame_id"] == current["frame_id"]
    assert "Repeated click region stalled" in refused["error"]
    assert len(factory.environments[0].executed) == 3


def test_computer_tool_allows_new_semantic_target_after_coordinate_stall() -> None:
    tool = ComputerTool(
        task_id="task-1",
        workspace=object(),  # type: ignore[arg-type]
        environment_factory=EnvironmentFactory(),
    )
    observation = make_observation("task-1", 1).model_copy(
        update={
            "elements": [
                ComputerElement(
                    element_id="snapshot-2:53",
                    source=ComputerElementSource.ACCESSIBILITY,
                    surface=ComputerElementSurface.DOCUMENT,
                    role="AXPopUpButton",
                    label="Open profile",
                    bounds=NormalizedRect(
                        x=0.744,
                        y=0.071,
                        width=0.014,
                        height=0.024,
                    ),
                )
            ]
        }
    )
    point_action = ComputerAction(
        type=ComputerActionType.CLICK,
        target=ComputerTarget(point=NormalizedPoint(x=0.751, y=0.083)),
    )
    for _ in range(3):
        assert tool._record_click_attempt("task-1", point_action, observation) is None

    semantic_action = ComputerAction(
        type=ComputerActionType.CLICK,
        target=ComputerTarget(
            element_id="snapshot-2:53",
            surface=ComputerElementSurface.DOCUMENT,
        ),
    )

    assert tool._record_click_attempt("task-1", semantic_action, observation) is None


@pytest.mark.asyncio
async def test_computer_screenshot_publishes_user_visible_artifact(tmp_path) -> None:
    factory = EnvironmentFactory()
    workspace = ArtifactWorkspace(tmp_path / "workspace")
    tool = ComputerTool(
        task_id="task-1",
        workspace=workspace,  # type: ignore[arg-type]
        environment_factory=factory,
    )
    initial = await tool.run_json_async({})

    captured = await tool.run_json_async({"action": "screenshot"})

    assert initial["observation"]["screenshot"]["file_ref"]["internal"] is True
    assert initial["delivery"] == "private_observation"
    assert "not a user-visible image" in initial["message"]
    assert captured["success"] is True
    assert captured["delivery"] == "user_visible_artifact"
    assert captured["file_ref"]["file_id"] == "public-1"
    assert captured["file_ref"].get("internal") is not True
    assert captured["inline_markdown"] == (
        f"![{captured['file_ref']['filename']}](file:public-1)"
    )
    assert Path(captured["file_ref"]["relative_path"]).parent.name == "output"
    output_path = workspace.workspace_dir / captured["file_ref"]["relative_path"]
    assert output_path.read_bytes() == b"png-bytes"
    assert captured[CONTEXT_REFS_KEY][0]["file_ref"]["internal"] is True


@pytest.mark.asyncio
async def test_computer_tool_does_not_attach_or_publish_reused_screenshot(
    tmp_path,
) -> None:
    factory = StaleScreenshotEnvironmentFactory()
    workspace = ArtifactWorkspace(tmp_path / "workspace")
    tool = ComputerTool(
        task_id="task-1",
        workspace=workspace,  # type: ignore[arg-type]
        environment_factory=factory,
    )

    initial = await tool.run_json_async({})
    captured = await tool.run_json_async({"action": "screenshot"})

    assert initial["success"] is True
    assert initial[CONTEXT_REFS_KEY] == []
    assert captured["success"] is False
    assert "temporarily unavailable" in captured["error"]


@pytest.mark.asyncio
async def test_computer_tool_rejects_known_unsupported_action_without_losing_frame() -> (
    None
):
    factory = RestrictedEnvironmentFactory()
    tool = ComputerTool(
        task_id="task-1",
        workspace=object(),  # type: ignore[arg-type]
        environment_factory=factory,
    )
    initial = await tool.run_json_async({})

    refused = await tool.run_json_async(
        {
            "expected_frame_id": initial["frame_id"],
            "action": "type",
            "text": "https://example.com",
        }
    )

    assert refused["success"] is False
    assert refused["frame_id"] == initial["frame_id"]
    assert "same_pid_keyboard_ambiguity" in refused["error"]
    assert "Do not retry" in refused["error"]
    assert factory.environments[0].current_observation is not None
    assert factory.environments[0].executed == []


@pytest.mark.asyncio
async def test_computer_tool_derives_session_from_task_and_step() -> None:
    factory = EnvironmentFactory()
    tool = ComputerTool(
        task_id="task-1",
        workspace=object(),  # type: ignore[arg-type]
        environment_factory=factory,
    )

    result = await tool.run_json_async(
        {
            "session_id": "invented-by-model",
            "_xagent_step_id": "inspect page",
        }
    )

    assert result["session_id"] == "task-1:inspect_page"
    assert factory.calls[0]["session_id"] == "task-1:inspect_page"


@pytest.mark.asyncio
async def test_computer_tool_lifts_action_scoped_expected_frame_id() -> None:
    factory = EnvironmentFactory()
    tool = ComputerTool(
        task_id="task-1",
        workspace=object(),  # type: ignore[arg-type]
        environment_factory=factory,
    )
    await tool.run_json_async({})

    result = await tool.run_json_async(
        {
            "actions": [
                {
                    "type": "click",
                    "expected_frame_id": "frame-1",
                    "target": {"point": {"x": 0.5, "y": 0.5}},
                }
            ]
        }
    )

    assert result["success"] is True
    assert factory.environments[0].executed[0].expected_frame_id == "frame-1"


@pytest.mark.asyncio
async def test_computer_tool_rejects_action_before_first_observation() -> None:
    factory = EnvironmentFactory()
    tool = ComputerTool(
        task_id="task-1",
        workspace=object(),  # type: ignore[arg-type]
        environment_factory=factory,
    )

    result = await tool.run_json_async(
        {"actions": [{"type": "navigate", "url": "https://example.com"}]}
    )

    assert result["success"] is False
    assert "observation" in result["error"]
    assert factory.environments[0].current_observation is None


@pytest.mark.asyncio
async def test_computer_tool_returns_validation_error_with_current_frame() -> None:
    factory = EnvironmentFactory()
    tool = ComputerTool(
        task_id="task-1",
        workspace=object(),  # type: ignore[arg-type]
        environment_factory=factory,
    )
    await tool.run_json_async({})

    result = await tool.run_json_async(
        {"actions": [{"type": "click", "target": {"point": {"x": 2, "y": 0}}}]}
    )

    assert result["success"] is False
    assert result["session_id"] == "task-1"
    assert result["frame_id"] == "frame-1"
    assert "Invalid computer action" in result["error"]
    assert len(factory.environments) == 1


def test_computer_tool_accepts_only_one_action_per_observation() -> None:
    with pytest.raises(ValueError, match="exactly one action object"):
        ComputerTool(
            task_id="task-1",
            workspace=object(),  # type: ignore[arg-type]
            environment_factory=EnvironmentFactory(),
        ).args_type().model_validate(
            {"actions": [{"type": "screenshot"}, {"type": "screenshot"}]}
        )


def test_computer_tool_exposes_flat_action_schema_and_accepts_legacy_shape() -> None:
    args_type = ComputerTool(
        task_id="task-1",
        workspace=object(),  # type: ignore[arg-type]
        environment_factory=EnvironmentFactory(),
    ).args_type()

    schema = args_type.model_json_schema()
    assert "action" in schema["properties"]
    assert "actions" not in schema["properties"]
    parsed = args_type.model_validate(
        {
            "expected_frame_id": "frame-1",
            "actions": [
                {
                    "type": "click",
                    "target": {"point": {"x": 0.5, "y": 0.5}},
                }
            ],
        }
    )
    assert parsed.action.value == "click"
    assert parsed.to_action().target is not None


@pytest.mark.asyncio
async def test_computer_tool_teardown_closes_created_environments(
    monkeypatch,
) -> None:
    factory = EnvironmentFactory()
    tool = ComputerTool(
        task_id="task-1",
        workspace=object(),  # type: ignore[arg-type]
        environment_factory=factory,
    )
    await tool.run_json_async({})

    async def no_browser_sessions(_task_id: str | None = None) -> None:
        return None

    monkeypatch.setattr(tool, "_close_task_sessions", no_browser_sessions)
    await tool.teardown()

    assert factory.environments[0].close_count == 1
    assert tool._environments == {}


def test_new_computer_observation_supersedes_stale_live_context() -> None:
    context = ExecutionContext()
    tool = ComputerTool(
        task_id="task-1",
        workspace=object(),  # type: ignore[arg-type]
        environment_factory=EnvironmentFactory(),
    )
    first = make_observation("task-1", 1)
    second = make_observation("task-1", 2)

    for observation in (first, second):
        result = {
            "success": True,
            "frame_id": observation.frame_id,
            "observation": observation.model_dump(mode="json"),
            CONTEXT_REFS_KEY: [observation.screenshot.durable_dict()],
            "_xagent_supersedes_scope": f"{tool.name}:task-1",
        }
        context.add_tool_result("computer", result)

    old_message, current_message = context.messages
    assert old_message.metadata["superseded"] is True
    assert old_message.metadata["raw_result"] == {
        "success": True,
        "superseded": True,
    }
    assert old_message.context_refs
    assert CONTEXT_REFS_KEY not in old_message.to_dict()
    assert old_message.context_refs_token_estimate() == 0
    assert "superseded by a later observation" in old_message.content
    assert CONTEXT_REFS_KEY in current_message.to_dict()
