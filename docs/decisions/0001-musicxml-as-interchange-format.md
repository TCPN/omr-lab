# ADR 0001: MusicXML as Initial Interchange Format

- Status: Proposed
- Date: 2026-10-10

## Context
The pipeline should compare multiple OMR engines while keeping conversion logic independent of each engine.

## Decision
Use MusicXML as the initial interchange format between adapters and normalization.

## Consequences
Common representation and independent validation are possible. Some engine-specific data may be lost; coordinates/confidence may not be present; parseable XML does not prove musical correctness.

## Follow-up
Validate real Audiveris and homr outputs before finalizing this decision.
