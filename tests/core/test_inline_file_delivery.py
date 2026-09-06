"""Explicit encoded artifacts share real FileRef delivery, not text heuristics."""

import base64
from pathlib import Path

import pytest

from xagent.core.inline_file_delivery import InlineFileDelivery, InlineFileStreamGuard


class Workspace:
    def __init__(self, root):
        self.workspace_dir = root
        self.output_dir = root / "output"
        self.files = {}

    def get_file_id_from_path(self, path):
        return self.files.get(path)

    def register_file(self, path):
        file_id = f"registered-{len(self.files)}"
        self.files[path] = file_id
        return file_id


def link(data=b"order,rate\r\nA01,0.15\r\n", name="report.csv", mime="text/csv"):
    return f"[{name}](data:{mime};base64,{base64.b64encode(data).decode()})"


@pytest.fixture
def delivery(tmp_path):
    return InlineFileDelivery(Workspace(tmp_path))


def test_registers_exact_bytes_and_deduplicates_repeated_deliveries(delivery):
    data = b"order,rate\r\nA01,0.15\r\n\x1a\x00\xff"
    source = f"Download: {link(data)}\n\nAgain: {link(data, name='copy.csv')}"
    result = delivery.transform(source)
    assert (
        result
        == "Download: [report.csv](file:registered-0)\n\nAgain: [report.csv](file:registered-0)"
    )
    assert len(delivery.workspace.files) == 1
    assert Path(next(iter(delivery.workspace.files))).read_bytes() == data
    assert delivery.transform(source) == result
    assert delivery.transform(result) == result
    assert len(delivery.workspace.files) == 1


@pytest.mark.parametrize("fence", ["```", "~~~~"])
def test_explicit_named_base64_fence(delivery, fence):
    source = f'{fence}base64 filename="report.csv"\nYSwK\nYiwK\n{fence}\n'
    assert delivery.transform(source) == "[report.csv](file:registered-0)\n"
    assert Path(next(iter(delivery.workspace.files))).read_bytes() == b"a,\nb,\n"


@pytest.mark.parametrize(
    "source",
    [
        "A long token: " + "YQ==" * 100,
        "`" + link() + "`",
        "``" + link() + "``",
        "`multi\n" + link() + "\nline`",
        "```python\n" + link() + "\n```",
        "~~~~text\n" + link() + "\n~~~~",
        "````markdown\n```base64 filename=report.csv\nYQ==\n```\n````",
        "```base64\nYQ==\n```",
        "```\n" + link(),
        "    " + link(),
        "> " + link(),
        "  > " + link(),
        "\\" + link(),
    ],
)
def test_does_not_turn_code_or_ambiguous_text_into_files(delivery, source):
    assert delivery.transform(source) == source
    assert not delivery.workspace.files


@pytest.mark.parametrize("payload", ["not-base64!!!", "Y", "YQ=", "", "YQ==garbage"])
def test_malformed_payload_has_no_raw_blob_or_fake_file(delivery, payload):
    result = delivery.transform(f"[report.csv](data:text/csv;base64,{payload})")
    assert result == "report.csv (attachment unavailable)"
    assert not delivery.workspace.files


def test_mime_controls_suffix_and_filename_cannot_escape_workspace(delivery):
    result = delivery.transform(link(name="../../outside.exe"))
    assert result == "[outside.csv](file:registered-0)"
    path = Path(next(iter(delivery.workspace.files)))
    assert path.is_relative_to(delivery.workspace.output_dir)
    assert path.name == "outside.csv"


def test_image_delivery_uses_inline_file_ref(delivery):
    result = delivery.transform("!" + link(b"image bytes", "figure.png", "image/png"))
    assert result == "![figure.png](file:registered-0)"


@pytest.mark.parametrize(
    "mime", ["text/html", "image/svg+xml", "application/x-executable"]
)
def test_active_or_unknown_types_are_not_materialized(delivery, mime):
    assert "attachment unavailable" in delivery.transform(link(mime=mime))
    assert not delivery.workspace.files


@pytest.mark.parametrize("budget", ["0", "-1", "invalid", "3"])
def test_budget_rejects_before_registration(delivery, monkeypatch, budget):
    monkeypatch.setenv("XAGENT_INLINE_FILE_DELIVERY_MAX_BYTES", budget)
    assert "attachment unavailable" in delivery.transform(link())
    assert not delivery.workspace.output_dir.exists()


def test_total_bytes_and_file_count_are_bounded(delivery, monkeypatch):
    monkeypatch.setenv("XAGENT_INLINE_FILE_DELIVERY_MAX_BYTES", "5")
    assert "file:" in delivery.transform(link(b"abc"))
    assert "attachment unavailable" in delivery.transform(link(b"def"))
    monkeypatch.setenv("XAGENT_INLINE_FILE_DELIVERY_MAX_BYTES", "1000")
    for index in range(7):
        assert "file:" in delivery.transform(link(bytes([index])))
    assert "attachment unavailable" in delivery.transform(link(b"ninth"))
    assert len(delivery.workspace.files) == 8


def test_registration_failure_is_logged_without_exposing_details(
    delivery, monkeypatch, caplog
):
    def fail(path):
        raise ValueError("server private detail")

    monkeypatch.setattr(delivery.workspace, "register_file", fail)
    assert delivery.transform(link()) == "report.csv (attachment unavailable)"
    assert not list(delivery.workspace.output_dir.iterdir())
    assert caplog.records[-1].exc_info[1].args == ("server private detail",)


def test_output_symlink_cannot_redirect_delivery(delivery, tmp_path):
    outside = tmp_path.parent / f"{tmp_path.name}-outside"
    outside.mkdir()
    delivery.workspace.output_dir.symlink_to(outside, target_is_directory=True)
    assert "attachment unavailable" in delivery.transform(link())
    assert not list(outside.iterdir())


@pytest.mark.parametrize("source", [link()[:-1], "```base64 filename=report.csv\nYQ=="])
def test_incomplete_explicit_deliveries_do_not_dump_encoded_content(delivery, source):
    assert delivery.transform(source) == "report.csv (attachment unavailable)"
    assert not delivery.workspace.files


def test_incomplete_link_does_not_swallow_following_text_or_deliveries(delivery):
    source = link()[:-1] + " See " + link(name="second.csv")
    assert (
        delivery.transform(source)
        == "report.csv (attachment unavailable) See [second.csv](file:registered-0)"
    )


def test_every_possible_stream_split_withholds_encoded_tail():
    source = "Download: " + link()
    for offset in range(len(source) + 1):
        guard = InlineFileStreamGuard()
        emitted = (
            guard.feed(source[:offset]) + guard.feed(source[offset:]) + guard.flush()
        )
        assert "base64" not in emitted
        assert "b3JkZX" not in emitted
        assert source.startswith(emitted)


def test_ordinary_stream_text_flushes_without_loss():
    guard = InlineFileStreamGuard()
    source = "Ordinary answer, no attachment."
    emitted = "".join(guard.feed(c) for c in source) + guard.flush()
    assert emitted == source


@pytest.mark.asyncio
@pytest.mark.parametrize("use_child_runtime", [False, True])
async def test_runtime_stream_end_and_buffered_result_share_registered_file(
    delivery, use_child_runtime
):
    from xagent.core.agent import PatternRuntime
    from xagent.core.agent.pattern.final_answer_stream import FinalAnswerStreamSession

    events = []
    runtime = PatternRuntime(
        outbound_message_handler=events.append, inline_file_delivery=delivery
    )
    stream_runtime = runtime
    if use_child_runtime:
        from xagent.core.agent.pattern.auto.auto import AutoPattern, _AutoChildRuntime

        stream_runtime = _AutoChildRuntime(
            parent=runtime, auto_pattern=AutoPattern(), root_context=None
        )
    stream = FinalAnswerStreamSession(stream_runtime)
    source = "Download: " + link()
    for char in source:
        await stream.emit_delta(char)
    await stream.finish(source)
    assert events[-1]["type"] == "final_answer_end"
    assert events[-1]["content"] == "Download: [report.csv](file:registered-0)"
    assert all("base64" not in str(e) for e in events)
    assert await stream_runtime.prepare_final_answer(source) == events[-1]["content"]
    assert len(delivery.workspace.files) == 1
    assert not runtime._inline_stream_guards


@pytest.mark.asyncio
async def test_runner_wires_delivery_and_normalizes_context(delivery):
    from xagent.core.agent import Agent
    from xagent.core.agent.runner import AgentRunner

    class Manager:
        def get_or_create_workspace(self, **kwargs):
            delivery.workspace.id = "inline-test"
            delivery.workspace.input_dir = delivery.workspace.workspace_dir / "input"
            delivery.workspace.temp_dir = delivery.workspace.workspace_dir / "temp"
            return delivery.workspace

    class Pattern:
        async def run(self, context, **kwargs):
            context.add_assistant_message(link())
            return {"success": True, "output": link()}

    runner = AgentRunner(
        Agent(name="inline-test", patterns=[Pattern()]), workspace_manager=Manager()
    )
    result = await runner.run("Create a CSV", execution_id="inline-test")
    assert result["output"] == "[report.csv](file:registered-0)"
    assert result["context"].messages[-1].content == result["output"]
    assert len(delivery.workspace.files) == 1
