"""Synthetic benchmark corpus: render MusicXML with Verovio and degrade it with
recorded seeds and parameters (docs/synthetic-corpus.md).

Rendering needs the optional ``synthetic`` extra (Verovio, cairosvg, Pillow, NumPy);
loading and checking a specification does not.
"""

from omr_lab.synthetic.generate import (
    IMAGES_DIRNAME,
    CorpusResult,
    SourceError,
    SyntheticDependencyError,
    generate_corpus,
    sample_seed,
)
from omr_lab.synthetic.spec import CorpusSpec, CorpusSpecError, load_corpus_spec

__all__ = [
    "IMAGES_DIRNAME",
    "CorpusResult",
    "CorpusSpec",
    "CorpusSpecError",
    "SourceError",
    "SyntheticDependencyError",
    "generate_corpus",
    "load_corpus_spec",
    "sample_seed",
]
