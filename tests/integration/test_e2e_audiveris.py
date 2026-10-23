"""End-to-end run of the committed sample score with a real Audiveris (issue #10).

Deselected by default; run with ``uv run pytest -m integration -s``. It skips when
Audiveris cannot be found (see docs/engines/audiveris.md for setup).

The test asserts only that the run produced output: exit code 0, a valid run
document with ``status: succeeded``, a loadable MusicXML artifact and a ``score``.
How well the music was recognized is measured against the sample's ground truth and
reported, not asserted, because a valid output is not a correct one. The report is
written to ``correctness.json`` in the run directory and printed (use ``-s`` to see it).

Set ``OMR_LAB_E2E_OUTPUT`` to a directory to keep the evidence: each run then writes
a new ``twinkle-<UTC timestamp>`` run directory there (the timestamp has microseconds)
instead of in pytest's temporary directory.
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path

import pytest
from e2e_report import IMAGE, SAMPLE, correctness, expected_score

from omr_lab.cli import EXIT_OK, main
from omr_lab.engines import AudiverisEngine
from omr_lab.musicxml import load_musicxml
from omr_lab.schemas import validate_run_document

pytestmark = pytest.mark.integration

OUTPUT_ENV_VAR = "OMR_LAB_E2E_OUTPUT"


@pytest.fixture(scope="module")
def audiveris_available() -> None:
    availability = AudiverisEngine().check_available()
    if not availability.available:
        pytest.skip(f"Audiveris not available: {availability.message}")


def _run_dir(tmp_path: Path) -> Path:
    root = os.environ.get(OUTPUT_ENV_VAR)
    if not root:
        return tmp_path / "run"
    # Microseconds keep repeated runs apart; the CLI refuses a non-empty directory.
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    return Path(root) / f"{SAMPLE['id']}-{stamp}"


def test_sample_end_to_end_with_audiveris(audiveris_available: None, tmp_path: Path) -> None:
    run_dir = _run_dir(tmp_path)

    code = main(["run", str(IMAGE), "-o", str(run_dir), "--engine", "audiveris"])

    # Output generation: asserted.
    document_path = run_dir / "run.json"
    assert document_path.is_file(), f"no run.json (exit code {code}); see the captured stderr"
    document = json.loads(document_path.read_text(encoding="utf-8"))
    validate_run_document(document)
    assert code == EXIT_OK, document.get("error")
    assert document["status"] == "succeeded"
    assert document["engine"]["name"] == "audiveris"
    load_musicxml(run_dir / document["artifacts"]["musicxml"])
    assert document["score"] is not None

    # Musical correctness: reported, not asserted.
    report = {
        "sample": SAMPLE["id"],
        "engine": document["engine"],
        "timing": document["timing"],
        "warnings": len(document["warnings"]),
        "correctness": correctness(expected_score(), document["score"]),
        "note": "Matched counts come from a longest-matching-blocks alignment of the "
        "note sequences; they are a rough indicator, not a benchmark metric.",
    }
    (run_dir / "correctness.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"\nEnd-to-end run directory: {run_dir}")
    print(json.dumps(report["correctness"], indent=2))
