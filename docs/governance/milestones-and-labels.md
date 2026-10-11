# Proposed Milestones and Labels

## Milestones
- M1 — Local OMR Foundation: OMR-001 through OMR-008.
- M2 — Benchmark and Preprocessing Baseline: OMR-009 through OMR-010.
- M3 — Engine Comparison: OMR-011 through OMR-012.
- M4 — Dewarping and Local Deployment: OMR-013 through OMR-014.

## Labels
- `type: feature`, `type: bug`, `type: docs`, `type: research`, `type: infrastructure`
- `priority: P0`, `priority: P1`, `priority: P2`
- `status: ready`, `status: blocked`
- `area: pipeline`, `area: engine`, `area: schema`, `area: preprocessing`, `area: benchmark`, `area: deployment`

## Apply labels and milestone on GitHub

Run the manual workflow at [Configure issue labels and milestone](../../.github/workflows/configure-issue-tracking.yml).

1. Open the repository's **Actions** tab and select **Configure issue labels and milestone**.
2. Choose **Run workflow** with the default unchecked **Apply changes** input for a dry run.
3. Review the run log. To make changes, run it again with **Apply changes** checked.
4. The workflow creates any missing labels and the open **M1 — Local OMR Foundation** milestone, then adds labels and assigns milestone M1 to Issues #3–#10.
5. Existing labels are preserved. If a label already exists, its current color and description are left unchanged. The workflow is safe to rerun.

The workflow has only a manual `workflow_dispatch` trigger and requests `issues: write` permission. It does not close issues, change issue bodies, assign people, or modify M2–M4.
