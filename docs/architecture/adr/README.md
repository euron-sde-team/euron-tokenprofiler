# Architecture decision records

| ADR | Decision |
| --- | --- |
| [0001](0001-single-process-sqlite.md) | A single API process with SQLite, no external database |
| [0002](0002-integer-micro-dollars.md) | Money is integer micro-dollars end to end |
| [0003](0003-estimator-calibration.md) | A calibrated approximate tokenizer, always flagged as estimated |
| [0004](0004-optimizer-overlap-cap.md) | Cap optimizer savings instead of summing overlapping rules |
| [0005](0005-per-app-isolation.md) | Per-key app allow-lists enforced in the query layer |
| [0006](0006-sdk-never-blocks.md) | The SDK and proxy must never block or fail the caller |
