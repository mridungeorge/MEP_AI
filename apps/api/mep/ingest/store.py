"""Service-role writer for ingest results: ingest_run + extraction evidence + space rows, in one transaction.

Everything written is provenance 'extracted' and unconfirmed (confirmed_by null): the engine refuses it until a
designer confirms it at Gate 1. A PDF (vision) result writes evidence only, never space rows.
"""
import json
import uuid
from dataclasses import asdict
from typing import Any

import psycopg
from psycopg.pq import TransactionStatus

from mep.ingest.health import HealthReport, score
from mep.ingest.records import ExtractionRecord, IngestResult

AREA_UNIT = "m^2"
VOID_UNIT = "mm"


class AlreadyIngested(Exception):
    """The same file (by SHA-256) was already ingested into this revision."""


def _health_json(report: HealthReport) -> dict[str, Any]:
    return {
        "score_percent": report.score_percent, "minimum_percent": report.minimum_percent,
        "below_threshold": report.below_threshold, "fixes": report.fixes, "recommendation": report.recommendation,
        "checks": {k: asdict(v) for k, v in report.checks.items()},
    }


def _evidence(result: IngestResult) -> list[ExtractionRecord]:
    """Space fields as extraction evidence (IFC and DXF), plus whatever the reader already emitted (PDF, Excel)."""
    rows = list(result.extractions)
    for s in result.spaces:
        fields: list[tuple[str, float | str, str | None]] = []
        if s.name is not None:
            fields.append(("name", s.name, None))
        if s.use is not None:
            fields.append(("use", s.use, None))
        if s.storey is not None:
            fields.append(("storey", s.storey, None))
        if s.area_m2 is not None:
            fields.append(("area_m2", s.area_m2, AREA_UNIT))
        if s.ceiling_void_mm is not None:
            fields.append(("ceiling_void_mm", s.ceiling_void_mm, VOID_UNIT))
        for name, value, unit in fields:
            rows.append(ExtractionRecord(result.source_kind, result.source_name, result.source_sha256, "space", s.key,
                                         name, value, unit, raw={"notes": list(s.notes)} if s.notes else {}))
    return rows


def store_ingest(conn: psycopg.Connection[Any], *, firm_id: str, revision_id: str, result: IngestResult) -> dict[str, Any]:
    """Write one ingest result. `conn` is a service-role connection with no open transaction."""
    if conn.info.transaction_status != TransactionStatus.IDLE:
        raise RuntimeError("the ingest connection must have no open transaction")
    if result.source_kind == "pdf" and result.spaces:
        raise ValueError("a PDF (vision) result may only write extraction evidence, never spaces")
    health = None if result.source_kind not in ("ifc", "dxf") else _health_json(score(result))
    run_id = str(uuid.uuid4())
    evidence = _evidence(result)
    with conn.transaction():
        try:
            conn.execute(
                "insert into ingest_run (id, firm_id, revision_id, source_kind, source_name, source_sha256, health,"
                " metadata, problems) values (%s, %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb, %s::jsonb)",
                (run_id, firm_id, revision_id, result.source_kind, result.source_name, result.source_sha256,
                 None if health is None else json.dumps(health), json.dumps(result.metadata, default=str),
                 json.dumps(result.problems)))
        except psycopg.errors.UniqueViolation as exc:
            raise AlreadyIngested(f"{result.source_name} was already ingested into this revision") from exc
        for e in evidence:
            conn.execute(
                "insert into extraction (firm_id, revision_id, ingest_run, source_kind, source_name, source_sha256,"
                " entity_kind, entity_key, field, value_number, value_text, value_bool, unit, confidence, raw)"
                " values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb)",
                (firm_id, revision_id, run_id, e.source_kind, e.source_name, e.source_sha256, e.entity_kind,
                 e.entity_key, e.field,
                 float(e.value) if isinstance(e.value, int | float) and not isinstance(e.value, bool) else None,
                 e.value if isinstance(e.value, str) else None, e.value if isinstance(e.value, bool) else None,
                 e.unit, e.confidence, json.dumps(e.raw)))
        for s in result.spaces:
            conn.execute(
                "insert into space (firm_id, revision_id, ifc_guid, name, use, storey, area_m2_value,"
                " area_m2_provenance, ceiling_void_mm_value, ceiling_void_mm_provenance, centroid_x_m, centroid_y_m)"
                " values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (firm_id, revision_id, s.key if result.source_kind == "ifc" else None, s.name, s.use, s.storey,
                 s.area_m2, None if s.area_m2 is None else "extracted", s.ceiling_void_mm,
                 None if s.ceiling_void_mm is None else "extracted",
                 None if s.centroid_m is None else s.centroid_m[0], None if s.centroid_m is None else s.centroid_m[1]))
    return {"ingest_run": run_id, "spaces": len(result.spaces), "extractions": len(evidence),
            "health": health}
