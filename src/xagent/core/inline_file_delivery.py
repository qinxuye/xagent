"""Turn explicit encoded-file deliveries into registered workspace attachments.

This is an output boundary, not a general Base64 detector or a format validator.
Only data-URI Markdown links outside code and explicitly named Base64 fences
opt in. Arbitrary prose, source code and unnamed encoded examples are preserved.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import logging
import re
import tempfile
from pathlib import Path
from threading import Lock
from typing import Any

from ..config import get_inline_file_delivery_max_bytes
from .file_ref import build_workspace_file_ref

logger = logging.getLogger(__name__)

# Never infer active HTML/SVG or executable types from untrusted model output.
_EXTENSIONS = {
    "text/plain": ".txt",
    "text/csv": ".csv",
    "text/tab-separated-values": ".tsv",
    "application/json": ".json",
    "application/pdf": ".pdf",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ".xlsx",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": ".pptx",
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/webp": ".webp",
    "image/gif": ".gif",
}
_DATA_LINK = re.compile(
    r"(?P<image>!)?\[(?P<label>[^\[\]\r\n]*)\]\("
    r"data:(?P<mime>[\w.+/-]+)(?:;charset=[\w-]+)?;base64,"
    r"(?P<payload>[^\s()\[\]]*)(?P<closed>\))?",
    re.IGNORECASE,
)
_FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})([^\r\n]*)")
_NAMED_BASE64 = re.compile(r"base64[ \t]+filename=([^\r\n]+)\Z", re.IGNORECASE)


class InlineFileStreamGuard:
    """Hold a possible encoded tail until the authoritative final replacement."""

    def __init__(self) -> None:
        self.pending = ""
        self.held = False

    def feed(self, delta: str) -> str:
        if self.held:
            return ""
        self.pending += delta
        lower = self.pending.lower()
        starts = [i for marker in ("data:", "base64") if (i := lower.find(marker)) >= 0]
        if starts:
            prefix = self.pending[: min(starts)]
            self.pending = ""
            self.held = True
            return prefix
        # Retain enough suffix to recognize markers split across any chunk edge.
        prefix, self.pending = self.pending[:-5], self.pending[-5:]
        return prefix

    def flush(self) -> str:
        pending, self.pending = self.pending, ""
        return pending


class InlineFileDelivery:
    """Per-run bounded, idempotent delivery; all I/O runs off the event loop."""

    def __init__(self, workspace: Any) -> None:
        self.workspace = workspace
        self._lock = Lock()
        self._refs: dict[tuple[str, str], dict[str, Any]] = {}
        self._bytes = 0

    def transform(self, content: str) -> str:
        if "base64" not in content.lower():
            return content
        with self._lock:
            return self._transform(content)

    def _transform(self, content: str) -> str:
        lines = content.splitlines(keepends=True)
        output: list[str] = []
        i = 0
        inline_ticks = 0
        while i < len(lines):
            line = lines[i]
            fence = _FENCE.match(line) if not inline_ticks else None
            if fence:
                marker, info = fence.groups()
                end = i + 1
                closing = re.compile(
                    r"^ {0,3}"
                    + re.escape(marker[0])
                    + "{"
                    + str(len(marker))
                    + r",}[ \t]*(?:\r?\n)?$"
                )
                while end < len(lines) and not closing.fullmatch(lines[end]):
                    end += 1
                named = _NAMED_BASE64.fullmatch(info.strip())
                if named:
                    filename = named.group(1).strip().strip('"')
                    suffix = Path(filename).suffix.lower()
                    mime = next(
                        (m for m, ext in _EXTENSIONS.items() if suffix == ext),
                        "",
                    )
                    replacement = self._deliver(
                        filename,
                        mime,
                        "".join(lines[i + 1 : end]) if end < len(lines) else "",
                    )
                    output.append(
                        replacement
                        + (
                            "\n"
                            if end < len(lines) and lines[end].endswith("\n")
                            else ""
                        )
                    )
                else:
                    output.extend(lines[i : min(end + 1, len(lines))])
                i = end + 1
                continue
            if line.startswith(("    ", "\t")) or re.match(r"^ {0,3}>", line):
                # Decline indented code/quoted examples, including nested fences.
                output.append(line)
                i += 1
                continue
            # Scan code delimiters and links together, so a backtick inside an
            # explicit link label cannot accidentally turn its payload into code.
            pieces: list[str] = []
            pos = 0
            while pos < len(line):
                if line[pos] == "`":
                    end = pos + 1
                    while end < len(line) and line[end] == "`":
                        end += 1
                    ticks = end - pos
                    if not inline_ticks:
                        inline_ticks = ticks
                    elif inline_ticks == ticks:
                        inline_ticks = 0
                    pieces.append(line[pos:end])
                    pos = end
                    continue
                match = (
                    _DATA_LINK.match(line, pos)
                    if not inline_ticks and line[pos] in "!["
                    else None
                )
                if match and (pos == 0 or line[pos - 1] != "\\"):
                    pieces.append(
                        self._deliver(
                            match["label"],
                            match["mime"].lower(),
                            match["payload"] if match["closed"] else "",
                            image=bool(match["image"]),
                        )
                    )
                    pos = match.end()
                else:
                    pieces.append(line[pos])
                    pos += 1
            output.append("".join(pieces))
            i += 1
        return "".join(output)

    def _deliver(
        self, label: str, mime: str, encoded: str, *, image: bool = False
    ) -> str:
        extension = _EXTENSIONS.get(mime)
        # Labels are never paths or authority to select an executable suffix.
        name = label.replace("\\", "/").rsplit("/", 1)[-1]
        name = (
            "".join(c for c in name if c.isalnum() or c in " ._-()").strip(" .")[:100]
            or "attachment"
        )
        if extension and not name.lower().endswith(extension):
            name = (Path(name).stem or "attachment") + extension
        unavailable = f"{name} (attachment unavailable)"
        if not extension:
            return unavailable
        try:
            limit = get_inline_file_delivery_max_bytes()
            if limit <= 0 or len(encoded) > ((limit + 2) // 3) * 4 + 4096:
                return unavailable
            encoded = re.sub(r"[\r\n\t ]", "", encoded)
            if len(encoded) > ((limit + 2) // 3) * 4:
                return unavailable
            data = base64.b64decode(encoded, validate=True)
            if not data or len(data) > limit:
                return unavailable
            key = (mime, hashlib.sha256(data).hexdigest())
            ref = self._refs.get(key)
            if ref is None:
                if len(self._refs) >= 8 or self._bytes + len(data) > limit:
                    return unavailable
                root = Path(self.workspace.workspace_dir).resolve()
                output = Path(self.workspace.output_dir).resolve()
                if not output.is_relative_to(root):
                    return unavailable
                output.mkdir(parents=True, exist_ok=True)
                directory = Path(tempfile.mkdtemp(prefix="inline-", dir=output))
                path = directory / name
                try:
                    with path.open("xb") as stream:
                        stream.write(data)
                    ref = build_workspace_file_ref(
                        workspace=self.workspace, file_path=path, mime_type=mime
                    )
                    if not ref.get("markdown_link"):
                        raise ValueError("Attachment registration returned no link")
                except Exception:
                    logger.exception("Inline file registration failed")
                    path.unlink(missing_ok=True)
                    directory.rmdir()
                    return unavailable
                self._refs[key] = ref
                self._bytes += len(data)
            link = str(ref["markdown_link"])
            return "!" + link if image and mime.startswith("image/") else link
        except (ValueError, binascii.Error):
            return unavailable
        except Exception:
            logger.exception("Inline file delivery failed")
            return unavailable
