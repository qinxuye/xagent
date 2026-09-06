"""Format checks are composable and never conflate execution with delivery."""

import hashlib
import os
import subprocess
import sys
from io import BytesIO
from unittest.mock import Mock
from zipfile import ZIP_DEFLATED, ZipFile

import pytest

from xagent.core.artifact_validation import (
    ArtifactCheck,
    ArtifactCheckRegistry,
    ArtifactContent,
    ValidationLimits,
    service,
)
from xagent.core.artifact_validation.defaults import default_registry
from xagent.core.artifact_validation.models import InvalidArtifact, UncheckedArtifact
from xagent.core.artifact_validation.service import validate_artifact


def check(data, filename, **limits):
    return default_registry().validate(
        ArtifactContent(filename, data, ValidationLimits(**limits))
    )


@pytest.mark.parametrize("extension", ["xlsx", "docx", "pptx", "pdf", "png"])
def test_corrupt_formats_are_invalid(extension):
    assert check(b"not a document", f"report.{extension}").status == "invalid"


@pytest.mark.parametrize("extension", ["xlsx", "docx", "pptx", "pdf", "png"])
def test_real_readers_accept_minimal_documents(extension):
    stream = BytesIO()
    if extension == "xlsx":
        from openpyxl import Workbook

        Workbook().save(stream)
    elif extension == "docx":
        from docx import Document

        Document().save(stream)
    elif extension == "pptx":
        from pptx import Presentation

        Presentation().save(stream)
    elif extension == "pdf":
        from pypdf import PdfWriter

        writer = PdfWriter()
        writer.add_blank_page(width=100, height=100)
        writer.write(stream)
    else:
        from PIL import Image

        Image.new("RGB", (4, 4)).save(stream, format="PNG")
    result = check(stream.getvalue(), f"file.{extension}")
    assert result.status == "valid", result


@pytest.mark.parametrize(
    "data",
    [
        b"",
        b"a,b\n1,2\n",
        b"one column",
        b"a;b\n1;2",
        b"a,b\n1,2,3",
        "名字,值\n甲,1".encode("utf-16"),
    ],
)
def test_csv_does_not_impose_business_schema(data):
    assert check(data, "data.CSV").status == "valid"


def test_csv_malformed_encoding_and_budget():
    assert check(b'a,b\n"unterminated,2', "data.csv").status == "invalid"
    assert check(b"\xff\x98", "data.csv").status == "unchecked"
    assert check(b"a,\x00", "data.csv").status == "invalid"
    assert check(b"a\nb", "data.csv", max_units=1).status == "unchecked"
    assert check(b"one", "data.csv", max_bytes=2).status == "unchecked"
    assert check(b"whatever", "data.unknown").status == "unchecked"


def test_archive_budgets_and_unsafe_xml():
    stream = BytesIO()
    with ZipFile(stream, "w", ZIP_DEFLATED) as archive:
        for name in ["[Content_Types].xml", "_rels/.rels", "xl/workbook.xml"]:
            archive.writestr(name, "<a/>" * 20)
    assert (
        check(stream.getvalue(), "data.xlsx", max_expanded_bytes=10).status
        == "unchecked"
    )
    stream = BytesIO()
    with ZipFile(stream, "w") as archive:
        for name in ["[Content_Types].xml", "_rels/.rels", "xl/workbook.xml"]:
            archive.writestr(name, '<!DOCTYPE a [<!ENTITY x "value">]><a>&x;</a>')
    assert check(stream.getvalue(), "data.xlsx").status == "invalid"


def test_image_pixel_budget_and_pdf_encryption():
    from PIL import Image
    from pypdf import PdfWriter

    stream = BytesIO()
    Image.new("RGB", (4, 4)).save(stream, format="PNG")
    assert check(stream.getvalue(), "image.png", max_pixels=4).status == "unchecked"
    stream = BytesIO()
    writer = PdfWriter()
    writer.add_blank_page(width=10, height=10)
    writer.encrypt("password")
    writer.write(stream)
    assert check(stream.getvalue(), "private.pdf").status == "unchecked"


@pytest.mark.parametrize(
    ("error", "status"),
    [
        (InvalidArtifact("bad structure"), "invalid"),
        (UncheckedArtifact("budget"), "unchecked"),
        (ImportError("missing"), "unchecked"),
        (RuntimeError("/secret/path"), "unchecked"),
    ],
)
def test_additional_checks_and_fail_closed_preflight(error, status):
    registry = ArtifactCheckRegistry()
    first = Mock()
    second = Mock(side_effect=error)
    third = Mock()
    for name, callback in [("first", first), ("second", second), ("third", third)]:
        registry.register(ArtifactCheck(name, frozenset({".foo"}), callback))
    result = registry.validate(ArtifactContent("new.foo", b"bytes", ValidationLimits()))
    assert result.status == status
    assert [c.name for c in result.checks] == ["first", "second"]
    assert "/secret/path" not in str(result.as_dict())
    third.assert_not_called()
    with pytest.raises(ValueError):
        registry.register(ArtifactCheck("first", frozenset({".foo"}), first))


def test_service_real_worker_and_same_name_rewrite(tmp_path):
    path = tmp_path / "test.csv"
    path.write_bytes(b"a,b\n1,2")
    first = validate_artifact(path)
    assert first.status == "valid"
    assert first.sha256 == hashlib.sha256(path.read_bytes()).hexdigest()
    path.write_bytes(b'a,b\n"broken')
    second = validate_artifact(path)
    assert second.status == "invalid"
    assert second.sha256 != first.sha256
    assert path.read_bytes() == b'a,b\n"broken'


def test_cache_is_byte_keyed_and_detects_mid_check_rewrite(tmp_path, monkeypatch):
    service._cache.clear()
    path = tmp_path / "test.csv"
    path.write_bytes(b"a,b")
    run = Mock(wraps=service._run_checks)
    monkeypatch.setattr(service, "_run_checks", run)
    assert validate_artifact(path).status == "valid"
    assert validate_artifact(path).status == "valid"
    assert run.call_count == 1
    service._cache.clear()

    def rewrite(filename, data, *_args):
        path.write_bytes(b"changed")
        return check(data, filename)

    monkeypatch.setattr(service, "_run_checks", rewrite)
    assert validate_artifact(path).status == "unchecked"


def test_worker_failure_timeout_missing_file_and_byte_budget(tmp_path, monkeypatch):
    service._cache.clear()
    path = tmp_path / "test.csv"
    path.write_bytes(b"abc")
    monkeypatch.setattr(
        service.subprocess,
        "run",
        Mock(side_effect=subprocess.TimeoutExpired("validator", 1)),
    )
    assert validate_artifact(path).status == "unchecked"
    monkeypatch.setenv("XAGENT_ARTIFACT_VALIDATION_MAX_BYTES", "2")
    assert validate_artifact(path).status == "unchecked"
    assert validate_artifact(tmp_path / "missing.csv").status == "unchecked"


def test_sandbox_cannot_assert_host_validation(tmp_path, monkeypatch):
    monkeypatch.setattr(service, "in_sandbox_tool_runner", lambda: True)
    assert validate_artifact(tmp_path / "data.csv").status == "unchecked"


def test_real_office_dependency_absence_is_unchecked(monkeypatch):
    from openpyxl import Workbook

    stream = BytesIO()
    Workbook().save(stream)
    monkeypatch.setitem(sys.modules, "openpyxl", None)
    report = check(stream.getvalue(), "file.xlsx")
    assert report.status == "unchecked"
    assert [c.status for c in report.checks] == ["valid", "unchecked"]


def test_invalid_configuration_preserves_an_unchecked_report(tmp_path, monkeypatch):
    monkeypatch.setenv("XAGENT_ARTIFACT_VALIDATION_MAX_BYTES", "invalid")
    assert validate_artifact(tmp_path / "file.csv").status == "unchecked"


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="FIFO not available")
def test_non_regular_files_do_not_block_snapshot_reads(tmp_path):
    path = tmp_path / "pipe.csv"
    os.mkfifo(path)
    assert validate_artifact(path).status == "unchecked"
