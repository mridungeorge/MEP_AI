"""Data types shared by the engine, runner and report."""
from dataclasses import dataclass, field
from datetime import date
from enum import StrEnum
from typing import Any

from mep.engine.jurisdiction import Override


class Provenance(StrEnum):
    ENGINEER_CONFIRMED = "engineer_confirmed"
    ADDRESS_LOOKUP_CONFIRMED = "address_lookup_confirmed"
    CALCULATED = "calculated"
    DEFAULT = "default"
    EXTRACTED = "extracted"


CONFIRMED = frozenset({Provenance.ENGINEER_CONFIRMED, Provenance.ADDRESS_LOOKUP_CONFIRMED, Provenance.CALCULATED})


class Outcome(StrEnum):
    PASS = "PASS"
    FAIL = "FAIL"
    NEEDS_JUDGEMENT = "NEEDS_JUDGEMENT"
    NOT_APPLICABLE = "NOT_APPLICABLE"


@dataclass(frozen=True)
class InputValue:
    """A value with its unit and provenance. Extracted values are refused until gate 1 confirms them."""

    value: Any
    unit: str | None = None
    provenance: Provenance = Provenance.ENGINEER_CONFIRMED
    confirmed: bool = True  # False when the stored row has no recorded Gate 1 confirmation (confirmed_by missing)


@dataclass(frozen=True)
class BuildingPart:
    """One part of a building: its NCC class, number of storeys and floor area (m^2). Mixed-use buildings have several."""

    building_class: str
    storeys: int | None = None
    area_m2: float | None = None


@dataclass
class ProjectFacts:
    """`building_class` is either one class as text (a one-part building) or a list of BuildingPart."""

    state: str
    ncc_edition: str
    climate_zone: int
    building_class: str | list[BuildingPart]
    approval_date: date
    firm_id: str = ""
    revision_id: str = ""
    facts_provenance: Provenance = Provenance.ENGINEER_CONFIRMED
    climate_zone_provenance: Provenance | None = None  # e.g. address_lookup_confirmed; defaults to facts_provenance


@dataclass
class Subject:
    """One thing under check (a system, duct run, pipe run, ...) and the rules assigned to it."""

    id: str
    rules: list[str]
    inputs: dict[str, InputValue] = field(default_factory=dict)
    part: int | None = None  # index into the project's building parts; required when there is more than one part


@dataclass
class RunRequest:
    project: ProjectFacts
    subjects: list[Subject]
    override: Override | None = None
