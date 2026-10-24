"""Load and check a synthetic corpus specification (docs/synthetic-corpus.md).

This module needs only the standard library and jsonschema, so a specification can be
checked without the rendering packages installed.
"""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from omr_lab.schemas.synthetic import (
    SyntheticCorpusSpecValidationError,
    validate_synthetic_corpus_spec,
)

DEFAULT_TEXT_FONTS: tuple[str, ...] = ("Liberation Serif", "Times New Roman")
"""Text fonts used when the specification names none: Verovio's own default is Times."""


@dataclass(frozen=True, slots=True)
class ParameterRule:
    """Allowed values of one degradation parameter."""

    minimum: float
    maximum: float
    integer: bool = False


# Kind -> parameter -> allowed range. Every parameter is required. The ranges are sanity
# limits against typos, not claims about realistic conditions.
DEGRADATION_PARAMETERS: dict[str, dict[str, ParameterRule]] = {
    # Counter-clockwise rotation in degrees; the canvas grows to keep the whole page.
    "rotate": {"degrees": ParameterRule(-45, 45)},
    # Each page corner moves inward by up to this fraction of the width and height.
    "perspective": {"strength": ParameterRule(0, 0.25)},
    # Vertical half-sine bend; peak shift as a fraction of the height (negative bends up).
    "curvature": {"amplitude": ParameterRule(-0.1, 0.1)},
    # Gaussian blur radius in pixels.
    "blur": {"radius": ParameterRule(0, 20)},
    # Standard deviation of Gaussian noise in grey levels (0-255).
    "noise": {"sigma": ParameterRule(0, 128)},
    # Linear darkening across the page: up to this fraction darker, towards angle degrees.
    "lighting": {"strength": ParameterRule(0, 0.9), "angle": ParameterRule(0, 360)},
    # JPEG re-encoding quality.
    "jpeg": {"quality": ParameterRule(1, 95, integer=True)},
}


def sample_id(source_id: str, variant_id: str) -> str:
    """The manifest id, image name and seed key of one source rendered as one variant."""
    return f"{source_id}-{variant_id}"


class CorpusSpecError(ValueError):
    """The specification cannot be read, is not valid JSON, or breaks its rules."""


@dataclass(frozen=True, slots=True)
class SourceSpec:
    id: str
    musicxml: Path
    musicxml_as_written: str
    """The ``musicxml`` value as written in the specification, for messages."""
    provenance: str
    """The manifest's ``source`` text."""
    license: str
    text_fonts: tuple[str, ...]
    work: str | None = None


@dataclass(frozen=True, slots=True)
class DegradationSpec:
    kind: str
    params: dict[str, float | tuple[float, float]]
    """A fixed value, or a (min, max) range drawn per sample."""


@dataclass(frozen=True, slots=True)
class VariantSpec:
    id: str
    condition: str
    degradations: tuple[DegradationSpec, ...]


@dataclass(frozen=True, slots=True)
class CorpusSpec:
    path: Path
    name: str
    description: str | None
    seed: int
    sources: tuple[SourceSpec, ...]
    variants: tuple[VariantSpec, ...]


def load_corpus_spec(path: Path | str) -> CorpusSpec:
    """Read and check a specification; raise CorpusSpecError with every problem found."""
    path = Path(path)
    try:
        document = json.loads(path.read_bytes().decode("utf-8"))
    except OSError as exc:
        raise CorpusSpecError(
            f"Corpus specification {str(path)!r} cannot be read: {exc.strerror or exc}."
        ) from exc
    except (UnicodeDecodeError, ValueError) as exc:
        raise CorpusSpecError(
            f"Corpus specification {str(path)!r} is not UTF-8 JSON: {exc}."
        ) from exc
    try:
        validate_synthetic_corpus_spec(document)
    except SyntheticCorpusSpecValidationError as exc:
        raise CorpusSpecError(f"Corpus specification {str(path)!r}: {exc}") from exc

    problems = _duplicate_ids("sources", document["sources"]) + _duplicate_ids(
        "variants", document["variants"]
    )
    variants = []
    for v_index, variant in enumerate(document["variants"]):
        degradations = []
        for d_index, degradation in enumerate(variant["degradations"]):
            where = f"$.variants[{v_index}].degradations[{d_index}]"
            params, found = _degradation_params(where, degradation)
            problems += found
            degradations.append(DegradationSpec(kind=degradation["kind"], params=params))
        variants.append(
            VariantSpec(
                id=variant["id"], condition=variant["condition"], degradations=tuple(degradations)
            )
        )
    sample_ids = Counter(
        sample_id(source["id"], variant["id"])
        for source in document["sources"]
        for variant in document["variants"]
    )
    clashes = sorted(sample_id for sample_id, count in sample_ids.items() if count > 1)
    if clashes and not problems:
        problems.append(
            "sample ids <source id>-<variant id> must be unique; repeated: " + ", ".join(clashes)
        )
    if problems:
        raise CorpusSpecError(f"Corpus specification {str(path)!r}: {'; '.join(problems)}")

    default_fonts = tuple(document.get("text_fonts", DEFAULT_TEXT_FONTS))
    sources = tuple(
        SourceSpec(
            id=source["id"],
            musicxml=path.parent / source["musicxml"],
            musicxml_as_written=source["musicxml"],
            provenance=source["source"],
            license=source["license"],
            text_fonts=tuple(source.get("text_fonts", default_fonts)),
            work=source.get("work"),
        )
        for source in document["sources"]
    )
    return CorpusSpec(
        path=path,
        name=document["name"],
        description=document.get("description"),
        seed=document["seed"],
        sources=sources,
        variants=tuple(variants),
    )


def _duplicate_ids(key: str, items: list[dict[str, Any]]) -> list[str]:
    repeated = sorted(i for i, count in Counter(item["id"] for item in items).items() if count > 1)
    return [f"$.{key}: ids must be unique; repeated: {', '.join(repeated)}"] if repeated else []


def _degradation_params(
    where: str, degradation: dict[str, Any]
) -> tuple[dict[str, float | tuple[float, float]], list[str]]:
    kind = degradation["kind"]
    rules = DEGRADATION_PARAMETERS[kind]
    given = {name: value for name, value in degradation.items() if name != "kind"}
    problems = [
        f"{where}: unknown parameter {name!r} for {kind!r}; expected: {', '.join(rules)}"
        for name in given
        if name not in rules
    ]
    problems += [
        f"{where}: {kind!r} needs parameter {name!r}" for name in rules if name not in given
    ]
    params: dict[str, float | tuple[float, float]] = {}
    for name, rule in rules.items():
        if name not in given:
            continue
        value = given[name]
        bounds = (value["min"], value["max"]) if isinstance(value, dict) else (value, value)
        if bounds[0] > bounds[1]:
            problems.append(f"{where}.{name}: min {bounds[0]} is greater than max {bounds[1]}")
        for bound in bounds:
            if not rule.minimum <= bound <= rule.maximum:
                problems.append(
                    f"{where}.{name}: {bound} is outside {rule.minimum:g} to {rule.maximum:g}"
                )
        params[name] = bounds if isinstance(value, dict) else value
    return params, problems
