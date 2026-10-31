# Run document fixtures

Synthetic examples of the normalized run document defined by
`src/omr_lab/schemas/run-0.3.0.schema.json` (see `docs/json-schema.md`).

- `valid/`: documents that must pass `validate_run_document()`.
- `invalid/`: documents that must fail; each file name states the single violation it contains.

All values are invented for testing; they do not describe real recognition results.
