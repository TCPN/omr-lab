# Benchmark Protocol

The manifest and report formats and the `omr-lab benchmark` command are in [benchmark.md](benchmark.md).

## Dataset
Use only scores with redistribution permission or keep private data outside Git. Record source/license, condition, checksum, and ground-truth availability. Include clean scans, phone photos, skew/perspective and curved pages only when relevant.

## Metrics
Record success/failure rate, engine version/options, wall-clock duration, output validity, human musical correctness review, defined ground-truth metrics when available, preprocessing time, and API cost when applicable.

## Fair comparison
Run all engines on the same manifest. Report sample count/exclusions and compatible metric definitions. Separate successful output generation from musical correctness. Preserve raw outputs for auditability.

## Dewarping
Compare original/corrected images with the same engine and settings. Evaluate pitch/rhythm/notation correctness as well as geometry; visually straighter staff alone is not success.
