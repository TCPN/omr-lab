# Normalized JSON Contract (Draft)

The first contract records only facts supported by the pipeline. Example shape:

```json
{
  "schema_version": "0.1.0",
  "status": "succeeded",
  "input": {"filename": "score.png", "media_type": "image/png"},
  "engine": {"name": "audiveris", "version": null},
  "artifacts": {"musicxml": "result.musicxml"},
  "timing": {"duration_seconds": 0.0},
  "score": null,
  "warnings": []
}
```

This is illustrative only; score: null is not recognized score content. OMR-003 must define formal JSON Schema, failed-run error shape, required/nullable fields, and positive/negative tests. Do not fabricate coordinates, confidence, or missing musical properties.
