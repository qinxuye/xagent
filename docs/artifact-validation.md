# Artifact format validation

Generated-file execution and delivery are separate outcomes. A successful
Python/JavaScript run or workspace write can still produce a corrupt file.
`FileRef.validation` and inline artifact metadata describe **format readability**,
not task completion, business correctness, or visual quality.

## Boundaries

- `core/artifact_validation/models.py`: immutable byte snapshot, check contract,
  limits and transport-neutral reports.
- `registry.py`: ordered checks selected by extension. Multiple checks may cover
  the same format. Stop at the first failure or unavailable preflight so later
  decoders cannot bypass safety limits.
- `defaults.py`: the composition root. It is the only registration list to edit
  when adding a format or another check; artifact discovery includes its suffixes.
- `service.py` / `worker.py`: host snapshot, concurrency limits, killable parser
  process, and a bounded cache keyed by SHA-256, filename and byte budget.
- Tools and web delivery consume the same report; neither contains format logic.
  No skill is required and no skill selection changes these checks.

States:

| Status | Meaning |
| --- | --- |
| `valid` | All applicable format checks completed successfully. |
| `invalid` | A checker established a format error; repair/recheck or report failure. |
| `unchecked` | Unsupported format, missing dependency, encrypted content, exhausted budget, changed file or checker failure. Never a pass. |

The report includes individual check names/messages and, when bytes were read,
the snapshot SHA-256. Exceptions exposed to users are generic, not parser traces
containing local paths or document content.

## Initial checks

- XLSX, DOCX, PPTX: bounded ZIP/CRC/XML preflight, then open with the actual format
  reader. XLSX walks cells without relying on worksheet dimension metadata.
- CSV/TSV: decode UTF-8 (optional BOM) or BOM-declared UTF-16 and parse records.
  Other encodings are unchecked. No required row count or rectangular schema.
- PDF: open structure and decode page content streams; encrypted PDFs unchecked.
- PNG/JPEG/GIF/WebP/BMP/TIFF: verify structure and decode image frames.

Empty templates are allowed. These checks do not recalculate formulas, render
office layouts, check source citations, verify semantic accuracy, inspect every
PDF resource, or certify that an embedded macro/link is safe to execute.
Legacy XLS, SVG, audio and video do not yet have format validators.

Optional Office readers come from `xagent[document-processing]`; missing readers
produce unchecked, not invalid. `defusedxml` is used before Office readers.

## Integration

`build_workspace_file_ref(..., validate=True)` opts a **completed output** into
validation. Python/JavaScript generated metadata and workspace writes use this
boundary. Other producers can opt in with the same keyword. Like registration,
this is blocking I/O: async producers must offload it to a worker thread.

Ordinary `TaskWorkspace.register_file`, inputs, temporary files and internal
references are unchanged. A validation failure never changes execution success,
deletes a file or loses its repair handle. File-reference instructions tell the
agent to repair invalid outputs or report the limitation, not to claim completion.

Sandbox reports are not host evidence. The host discards guest validation and
checks re-registered host bytes. Failed registration remains an unavailable
reference, never a passed check.

Authenticated and public preview routes accept `validation_only=true`, after
their existing authorization/path checks. Validation bypasses redirects, reads
the actual local/materialized file and returns no-store JSON with media type
`application/vnd.xagent.validation+json` (never confused with attachment JSON).
Downloads and
ordinary byte previews remain available, including for invalid files.

Inline attachments fetch this server-authoritative status independently of tool
traces or skills. The status is separate from the renderer: invalid files retain
download/repair access. Recheck refreshes the report; unsupported audio/video
keep existing progressive playback without an extra validation fetch.

Limits: default 32 MiB input, 8-second parser wall time, two active snapshots per
host process, 64 MiB expanded Office/PDF content, 2,048 ZIP entries, 200,000 cells
or CSV rows, 500 PDF pages and 25 million image-frame pixels. Linux workers also
have a 1 GiB address-space ceiling. The process is resource isolation, **not** a
security sandbox. On macOS portable format budgets and wall-time limits apply.
Configure input/time via `XAGENT_ARTIFACT_VALIDATION_MAX_BYTES` and
`XAGENT_ARTIFACT_VALIDATION_TIMEOUT_SECONDS` in `config.py` / `example.env`.

## Add a check

Implement a callable accepting `ArtifactContent` and register it in
`default_registry()` with `ArtifactCheck("unique-name", frozenset({".ext"}), fn)`.
Successful checks return `None`. Raise `InvalidArtifact` only for a known format
error, or `UncheckedArtifact` when validity cannot be established. Messages must
be safe for model/user output. Lazy-import optional parsers inside the callable.

Checks must use only the provided bytes, not paths, network access or mutation.
Put cheap structural/expansion preflights before expensive readers. A new check
for an existing extension composes with its current checks; no executor or UI
changes are necessary. Restart workers after changing the composition root.
Add valid, corrupt, budget, dependency and rewrite tests in
`tests/core/test_artifact_validation.py`.

Markdown table diagnostics and Base64-to-attachment conversion are separate
follow-ups, not part of this file-readability contract.
