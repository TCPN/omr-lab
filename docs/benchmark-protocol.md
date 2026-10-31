# Benchmark Protocol

The manifest and report formats and the `omr-lab benchmark` command are in [benchmark.md](benchmark.md).

## Dataset
Use only scores with redistribution permission or keep private data outside Git. Record source/license, condition, checksum, and ground-truth availability. Include clean scans, phone photos, skew/perspective and curved pages only when relevant.

## Metrics
Record success/failure rate, engine version/options, wall-clock duration, output validity, human musical correctness review, defined ground-truth metrics when available ([metrics.md](metrics.md)), preprocessing time, and API cost when applicable.

## Fair comparison
Run all engines on the same manifest. Report sample count/exclusions and compatible metric definitions. Separate successful output generation from musical correctness. Preserve raw outputs for auditability.

## Preprocessing
Preprocessing is optional and does not always help ([preprocessing.md](preprocessing.md)). To judge a step, run the same manifest twice with the same engine, options and timeout, once without `--preprocess` and once with it, and compare the correctness totals per condition, not only success rates. The report records the requested steps and, per sample, which steps changed the image; each run document records the parameters, measurements and the preprocessed file. Keep the comparison per condition: a step that helps phone photos can be neutral or harmful on clean renderings.

## Dewarping
Compare original/corrected images with the same engine and settings. Evaluate pitch/rhythm/notation correctness as well as geometry; visually straighter staff alone is not success.
