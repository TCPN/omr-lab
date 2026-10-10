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
- engines/: engine-specific availability and process invocation.
- preprocess/: optional, independently testable image transforms.
- musicxml/: validation/parsing independent of engine.
- conversion/: normalized JSON mapping.
- pipeline.py: orchestration only.
- reporting/: run metadata and benchmark reports.

## Rules
MusicXML is the initial interchange format. Keep engine-specific arguments in adapters. Unit tests must not require Java, network, model downloads, or paid APIs. A successful process exit does not prove musical correctness. Do not fabricate image coordinates or confidence. Dewarping is a separately evaluated transform; measure downstream music correctness, not only visual straightness.

## Candidates
Audiveris is the initial integration candidate, subject to verified CLI behavior and license review. homr is the second candidate. Flat OMR API is a possible later cloud adapter gated by credentials, cost controls, privacy/terms review, and explicit approval.

## Cloud direction
Keep schema, parsing, and engine interface independent of hosting. Add a service/API layer only after local runs are repeatable and benchmarked.
