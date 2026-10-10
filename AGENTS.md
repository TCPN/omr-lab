# OMR Lab — Agent Instructions

## Goal
Build a modular OMR pipeline: image/PDF → optional preprocessing → engine adapter → MusicXML validation → normalized JSON. Local-first; cloud deployment is a later phase.

## Read first
Read README.md, this file, docs/architecture.md, relevant schema/decision docs, and the assigned GitHub Issue. If documents conflict, report the conflict before broad changes. Unrecorded chat context is not authoritative.

## Engineering
- Python src layout; pyproject.toml; uv dependency management.
- Type hints for public interfaces; use pathlib for paths.
- External programs: subprocess.run with argument lists, timeouts, captured return codes/output; never shell-concatenate untrusted paths.
- Do not swallow exceptions silently, overwrite source images, commit secrets, or commit private/unlicensed scores.
- Keep generated artifacts in explicit output/temp directories.

## Engine boundary
Engine-specific invocation belongs in engines/, not pipeline.py. Check actual availability; do not assume a tool is installed. Report missing dependencies with actionable messages. Keep raw engine artifacts separate from normalized JSON.

## Data contract
MusicXML is the initial interchange format. JSON must follow docs/json-schema.md and the checked-in schema. Preserve input, engine/version when available, options, status, warnings, and timing. Never invent coordinates, confidence scores, or musical properties not present in the source output.

## Tests
Add/update pytest tests and run uv run ruff check . and uv run pytest. Unit tests must not require Java, network, model downloads, or API credentials. Real engine checks are integration tests; if not run, explain why. A valid output file is not proof of correct musical recognition.

## Security and dependencies
Keep dependencies minimal; document executable/model sources; never log secrets or entire environment variables. Review upstream licenses and flag AGPL obligations for human review before distribution/deployment. Do not add cloud services without an issue and explicit approval.

## Git workflow
Work only on the assigned Issue; keep changes small; do not weaken tests to pass CI; update docs for public interface/schema changes. Do not push directly to the default branch unless explicitly instructed. Report changed files, commands/results, unrun tests, and limitations.

## Definition of done
Acceptance criteria met; relevant tests and lint pass; docs updated; no secrets or unauthorized data; remaining limitations documented.
