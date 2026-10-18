"""Deterministic mock engine for tests and demos; it performs no recognition.

The mock engine lets the pipeline (and later the CLI) run end to end without
Java, models, or any installed OMR engine. It ignores the input image and
always writes the same placeholder MusicXML (one empty measure, no notes), so
its output must never be mistaken for a recognition result: the run document
records ``engine.name == "mock"``.
"""

from __future__ import annotations

from pathlib import Path

from omr_lab.engines.base import (
    EngineAvailability,
    EngineOptions,
    EngineResult,
    InvalidEngineOptionError,
    resolve_options,
)

MOCK_ENGINE_VERSION = "0.1.0"

MOCK_MUSICXML_FILENAME = "mock.musicxml"
MOCK_LOG_FILENAME = "mock.log"

# Fixed placeholder: a single part with one empty measure. It carries no notes,
# so the mock never presents fabricated musical content as recognized.
_PLACEHOLDER_MUSICXML = """\
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE score-partwise PUBLIC "-//Recordare//DTD MusicXML 4.0 Partwise//EN" \
"http://www.musicxml.org/dtds/partwise.dtd">
<score-partwise version="4.0">
  <part-list>
    <score-part id="P1">
      <part-name>Mock placeholder</part-name>
    </score-part>
  </part-list>
  <part id="P1">
    <measure number="1"/>
  </part>
</score-partwise>
"""

_OUTCOMES = ("succeeded", "failed")


class MockEngine:
    """Engine adapter that writes fixed placeholder artifacts without recognizing anything.

    ``available=False`` simulates missing prerequisites. The ``outcome`` option
    (``"succeeded"`` by default, or ``"failed"``) selects a successful or a
    failed result; both write a log artifact so failure handling can be tested.
    """

    name = "mock"
    default_options: EngineOptions = {"outcome": "succeeded"}

    def __init__(self, *, available: bool = True) -> None:
        self._available = available

    def check_available(self) -> EngineAvailability:
        if self._available:
            return EngineAvailability(
                engine_name=self.name, available=True, version=MOCK_ENGINE_VERSION
            )
        return EngineAvailability(
            engine_name=self.name,
            available=False,
            message="Mock engine was configured as unavailable (MockEngine(available=False)).",
        )

    def recognize(
        self,
        input_path: Path,
        output_dir: Path,
        *,
        timeout_seconds: float,
        options: EngineOptions | None = None,
    ) -> EngineResult:
        del timeout_seconds  # Nothing runs long enough to time out.
        effective = resolve_options(self.name, options, self.default_options)
        outcome = effective["outcome"]
        if outcome not in _OUTCOMES:
            raise InvalidEngineOptionError(
                f"Option 'outcome' for engine {self.name!r} must be one of "
                f"{', '.join(_OUTCOMES)}, got {outcome!r}."
            )

        output_dir.mkdir(parents=True, exist_ok=True)
        log_path = output_dir / MOCK_LOG_FILENAME
        if outcome == "failed":
            log_path.write_text(
                f"mock engine: simulated failure for {input_path.name}\n", encoding="utf-8"
            )
            return EngineResult(
                engine_name=self.name,
                success=False,
                artifacts={"engine_log": log_path},
                version=MOCK_ENGINE_VERSION,
                diagnostics=("Mock engine simulated a failure (outcome=failed).",),
                returncode=1,
                error=f"Mock engine simulated a failure for {input_path.name}.",
                options=effective,
            )

        musicxml_path = output_dir / MOCK_MUSICXML_FILENAME
        musicxml_path.write_text(_PLACEHOLDER_MUSICXML, encoding="utf-8")
        log_path.write_text(
            f"mock engine: wrote placeholder MusicXML for {input_path.name}; "
            "no recognition was performed\n",
            encoding="utf-8",
        )
        return EngineResult(
            engine_name=self.name,
            success=True,
            artifacts={"musicxml": musicxml_path, "engine_log": log_path},
            version=MOCK_ENGINE_VERSION,
            diagnostics=("Mock engine performed no recognition; the MusicXML is a placeholder.",),
            returncode=0,
            options=effective,
        )
