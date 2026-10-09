"""Records every ingest reader produces. Readers return these; none of them decides compliance or confirms anything.

Every value read from a file carries provenance EXTRACTED: the engine refuses it until a designer confirms it at Gate 1.
Numbers carry a unit. Areas are m^2, heights mm (the database fixes these units per column).
"""
from dataclasses import dataclass, field
from typing import Any

SOURCE_KINDS = ("ifc", "dxf", "pdf", "xlsx")


class IngestRefused(ValueError):
    """The reader refuses the file on purpose (too complex, unsupported): the message is safe to show the user."""


@dataclass(frozen=True)
class SpaceRecord:
    """One room/space read from a model or drawing."""

    key: str                          # IFC GlobalId, DXF entity handle, or PDF label
    name: str | None = None
    area_m2: float | None = None
    use: str | None = None
    storey: str | None = None
    ceiling_void_mm: float | None = None
    source_kind: str = "ifc"
    provenance: str = "extracted"
    notes: tuple[str, ...] = ()       # reader remarks (e.g. 'area from geometry, not from a Qto')
    centroid_m: tuple[float, float] | None = None   # plan position in metres, where the reader can tell (revision matching)


@dataclass(frozen=True)
class ExtractionRecord:
    """One extracted field, destined for the `extraction` table (evidence only; never an input)."""

    source_kind: str
    source_name: str
    source_sha256: str
    entity_kind: str                  # space | system | equipment
    entity_key: str
    field: str
    value: float | str | bool
    unit: str | None = None           # required for numbers
    confidence: float | None = None   # 0..1, only where the reader has one
    raw: dict[str, Any] = field(default_factory=dict)
    provenance: str = "extracted"

    def __post_init__(self) -> None:
        if self.source_kind not in SOURCE_KINDS:
            raise ValueError(f"unknown source kind {self.source_kind!r}")
        if self.provenance != "extracted":
            raise ValueError("extraction records are always provenance 'extracted'")
        if isinstance(self.value, bool) or not isinstance(self.value, int | float):
            if self.unit is not None and not isinstance(self.value, bool):
                raise ValueError("text values have no unit")
        elif self.unit is None:
            raise ValueError("a numeric extraction needs a unit")


@dataclass
class IngestResult:
    """What a reader returns: records plus the checks it ran, for the health score."""

    source_kind: str
    source_name: str
    source_sha256: str
    spaces: list[SpaceRecord] = field(default_factory=list)
    extractions: list[ExtractionRecord] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)      # exporter, schema, units, scale ...
    problems: list[str] = field(default_factory=list)           # things the reader could not read


def result_to_json(result: IngestResult) -> dict[str, Any]:
    """A plain-JSON form of a reader's result (used to pass it out of the sandboxed reader process)."""
    from dataclasses import asdict
    return asdict(result)


def result_from_json(data: dict[str, Any]) -> IngestResult:
    spaces = [SpaceRecord(**{**s, "notes": tuple(s.get("notes", ())),
                             "centroid_m": None if s.get("centroid_m") is None else tuple(s["centroid_m"])})
              for s in data.get("spaces", [])]
    extractions = [ExtractionRecord(**e) for e in data.get("extractions", [])]
    return IngestResult(data["source_kind"], data["source_name"], data["source_sha256"], spaces, extractions,
                        dict(data.get("metadata", {})), list(data.get("problems", [])))
