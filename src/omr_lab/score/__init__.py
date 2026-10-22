"""The normalized score model (docs/score-model.md) and converters into it.

The model records what the source says about the music; a converted score is not
evidence that the recognition was correct.
"""

from omr_lab.score.from_musicxml import (
    IGNORED_ELEMENTS,
    ScoreConversion,
    ScoreWarning,
    rational,
    score_from_musicxml,
)

__all__ = [
    "IGNORED_ELEMENTS",
    "ScoreConversion",
    "ScoreWarning",
    "rational",
    "score_from_musicxml",
]
