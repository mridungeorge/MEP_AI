"""Cited report: JSON and PDF. Every output carries the draft banner while any rule used is a draft."""
import json
from collections import Counter
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from mep.engine.jurisdiction import Decision
from mep.engine.loader import Rule
from mep.engine.model import ProjectFacts

DRAFT_BANNER = "DRAFT RULES: NOT ENGINEER-APPROVED"
REPORT_VERSION = 1
ENGINE_VERSION = "0.1.0"
OUTCOMES = ("PASS", "FAIL", "NEEDS_JUDGEMENT", "NOT_APPLICABLE")


def build_report(
    *,
    project: ProjectFacts,
    decision: Decision,
    override: dict[str, str] | None,
    used_rules: list[Rule],
    results: list[dict[str, Any]],
    unassigned_rules: list[str],
    refused_by: list[str],
) -> dict[str, Any]:
    drafts = [r.id for r in used_rules if r.status != "approved"]  # draft (or anything not approved)
    counts = Counter(r["outcome"] for r in results)
    if override is not None:
        jurisdiction_state = "overridden"
    else:
        jurisdiction_state = "confirmed"
    return {
        "banner": DRAFT_BANNER if (drafts or not results) else None,  # an empty run is not an approved one
        "report_version": REPORT_VERSION,
        "engine": {"name": "mep-engine", "version": ENGINE_VERSION},
        "draft_rules": drafts,
        "unassigned_rules": unassigned_rules,  # selected for this project but assigned to no subject
        "project": {
            "state": project.state.upper(), "ncc_edition": project.ncc_edition, "climate_zone": project.climate_zone,
            **_building_json(project.building_class), "approval_date": project.approval_date.isoformat(),
        },
        "jurisdiction": {
            "decision": jurisdiction_state,
            "warnings": list(decision.reasons),
            "refused_by": refused_by,
            "notes": list(decision.notes),
            "override": override,
        },
        "rule_pack": [{"id": r.id, "status": r.status, "sha256": r.sha256} for r in used_rules],
        "summary": {o: counts.get(o, 0) for o in OUTCOMES},
        "results": results,
    }


def _building_json(building_class: Any) -> dict[str, Any]:
    """`building_class` stays the plain class for a one-part building; a mixed-use building reports 'mixed' + parts."""
    if isinstance(building_class, str):
        return {"building_class": building_class}
    parts = [{"index": i, "building_class": x.building_class, "storeys": x.storeys, "area_m2": x.area_m2}
             for i, x in enumerate(building_class)]
    out: dict[str, Any] = {"building_class": building_class[0].building_class if len(building_class) == 1 else "mixed"}
    if len(building_class) > 1 or parts[0]["storeys"] is not None or parts[0]["area_m2"] is not None:
        out["building_parts"] = parts
    return out


def _parts_text(project: dict[str, Any]) -> str:
    parts = project.get("building_parts")
    if not parts or len(parts) < 2:
        return ""
    return " (" + "; ".join(f"part {x['index']}: class {x['building_class']}" for x in parts) + ")"


def to_json(report: dict[str, Any]) -> str:
    return json.dumps(report, indent=2, ensure_ascii=False)


def to_pdf(report: dict[str, Any], path: Path) -> None:
    banner = report.get("banner")

    def decorate(canvas: Any, doc: Any) -> None:
        canvas.saveState()
        width, height = landscape(A4)
        if banner:
            canvas.setFillColor(colors.HexColor("#B00020"))
            canvas.setFont("Helvetica-Bold", 12)
            canvas.drawCentredString(width / 2, height - 10 * mm, banner)
        canvas.setFillColor(colors.black)
        canvas.setFont("Helvetica", 8)
        canvas.drawString(12 * mm, 8 * mm, f"MEP Co-pilot report v{REPORT_VERSION}  |  page {doc.page}")
        canvas.restoreState()

    styles = getSampleStyleSheet()
    cell = ParagraphStyle("cell", parent=styles["BodyText"], fontSize=7, leading=9)
    doc = SimpleDocTemplate(str(path), pagesize=landscape(A4), topMargin=18 * mm, bottomMargin=14 * mm,
                            leftMargin=12 * mm, rightMargin=12 * mm, title="Compliance report")
    project = report["project"]
    story: list[Any] = [
        Paragraph("Compliance report", styles["Title"]),
        Paragraph(escape(f"{project['ncc_edition']} | {project['state']} | climate zone {project['climate_zone']} | "
                         f"class {project['building_class']}{_parts_text(project)} | approval date {project['approval_date']}"),
                  styles["Normal"]),
        Paragraph("Summary: " + ", ".join(f"{k} {v}" for k, v in report["summary"].items()), styles["Normal"]),
        Paragraph(f"Jurisdiction: {report['jurisdiction']['decision']}", styles["Normal"]),
    ]
    if report["jurisdiction"]["override"]:
        ov = report["jurisdiction"]["override"]
        story.append(Paragraph(escape(f"Override by approver {ov['user_id']}: {ov['reason']}"), styles["Normal"]))
        story += [Paragraph(escape(f"Refusal reason: {w}"), styles["Normal"])
                  for w in report["jurisdiction"]["warnings"]]
    story.append(Spacer(1, 4 * mm))
    rows: list[list[Any]] = [[Paragraph(h, cell) for h in ("Subject", "Rule", "Clause", "Outcome", "Notes")]]
    for r in report["results"]:
        notes = []
        if r["causes"]:
            notes.append("; ".join(f"{c['kind']}: {c['detail']}" for c in r["causes"]))
        if r["near_miss"] and r["near_miss"]["is_near_miss"]:
            notes.append("NEAR MISS")
        notes += r["fix_hypotheses"]
        rows.append([Paragraph(escape(str(v)), cell) for v in (
            r["subject_id"], r["rule_id"], f"{r['citation']['document']} {r['citation']['clause']}",
            r["outcome"])] + [Paragraph("<br/>".join(escape(n) for n in notes) or "-", cell)])
    table = Table(rows, repeatRows=1, colWidths=[28 * mm, 62 * mm, 55 * mm, 26 * mm, 95 * mm])
    table.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.25, colors.grey), ("BACKGROUND", (0, 0), (-1, 0), colors.lightgrey),
                               ("VALIGN", (0, 0), (-1, -1), "TOP")]))
    story.append(table)
    doc.build(story, onFirstPage=decorate, onLaterPages=decorate)
