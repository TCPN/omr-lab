# Architecture

## Initial data flow
1. Validate input path/type.
2. Optionally preprocess a copy.
3. Call a selected OMR engine through an adapter.
4. Store raw artifacts in the run output directory.
5. Validate and parse MusicXML.
6. Normalize available information into project JSON.
7. Write a run record with provenance and timing.

## Boundaries
- engines/: engine-specific availability and process invocation; shared contracts live in engines/base.py.
- preprocess/: optional, independently testable image transforms.
- musicxml/: loading, safe parsing, and structural validation of MusicXML artifacts (including .mxl) plus metadata extraction, independent of engine. A document that loads is parseable, not correctly recognized. Details: docs/musicxml.md.
- conversion/: normalized JSON mapping.
- pipeline.py: orchestration only.
- reporting/: run metadata and benchmark reports.

## Engine contract
- Each adapter implements the Engine protocol: name, check_available(), and recognize().
- recognize() takes an optional, keyword-only `options` mapping of JSON-scalar engine settings. Adapters reject unknown option names with an actionable error (`resolve_options`) and echo the effective options in EngineResult so the run record can store them. Details: docs/engine-interface.md.
- Availability reports include whether the engine is runnable, optional version metadata, and an actionable message.
- Recognition results include success/failure, engine name/version, raw artifact paths, diagnostics, return code, optional error text, and the effective options.
- External processes are launched with an argument sequence, captured stdout/stderr (decoded as UTF-8), and a finite timeout that terminates the whole process tree. Do not construct shell command strings from paths or user input. Details: docs/engine-interface.md.
- Unit tests use fake engines and monkeypatched subprocess calls; they must not require Java, network access, model downloads, or real OMR engines.
- A successful process or adapter result does not prove musical correctness. Recognition accuracy must be assessed separately.

## Rules
MusicXML is the initial interchange format. Keep engine-specific arguments in adapters. Unit tests must not require Java, network, model downloads, or paid APIs. A successful process exit does not prove musical correctness. Do not fabricate image coordinates or confidence. Dewarping is a separately evaluated transform; measure downstream music correctness, not only visual straightness.

## Candidates
Audiveris is the initial integration candidate, subject to verified CLI behavior and license review. homr is the second candidate. Flat OMR API is a possible later cloud adapter gated by credentials, cost controls, privacy/terms review, and explicit approval.

## Cloud direction
Keep schema, parsing, and engine interface independent of hosting. Add a service/API layer only after local runs are repeatable and benchmarked.
