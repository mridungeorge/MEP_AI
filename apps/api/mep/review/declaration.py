"""The NSW design compliance declaration, as a DRAFT prefilled from the signed package.

This writes NO legal statement and lodges NOTHING. It gathers the facts the registered design practitioner will need (building, scope, who signed which gate with
which registration number, what is open) and lists the fields they must complete themselves. Every page carries the banner; the practitioner reviews and lodges it
on the NSW Planning Portal.

Applies to NSW projects with a building part of class 2, 3 or 9c. Available only once all three gates are signed and the ledger verifies.
"""
import hashlib
import io
from typing import Any
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

BANNER = "DRAFT: the registered design practitioner must review and lodge on the NSW Planning Portal"
APPLICABLE_CLASSES = ("2", "3", "9c")
TO_COMPLETE = (
    ("declaration_statement", "The declaration itself (the practitioner's statement under the NSW legislation). Not generated here: it is the practitioner's to write or select."),
    ("practitioner_identity", "The registered design practitioner's name, registration class and number as the Planning Portal requires them."),
    ("planning_portal_reference", "The Planning Portal application or lodgement reference."),
    ("scope_of_design_work", "The precise scope of design work and the documents the declaration refers to."),
    ("performance_solutions", "Any performance solution relied on, with the practitioner's own assessment (see the list below)."),
)


class NotApplicable(Exception):
    """The project is not an NSW class 2, 3 or 9c project, or its package is not complete."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code, self.message = code, message


def classes_of(package: dict[str, Any]) -> list[str]:
    return sorted({str(p["class"]).strip().lower() for p in package["project"]["building_parts"]})


def build(package: dict[str, Any], pathways: list[dict[str, Any]]) -> dict[str, Any]:
    project = package["project"]
    if str(project["state"]).upper() != "NSW":
        raise NotApplicable("not_nsw", "this declaration is for NSW projects")
    classes = classes_of(package)
    hit = [c for c in classes if c in APPLICABLE_CLASSES]
    if not hit:
        raise NotApplicable("class_not_covered", f"this declaration is for building classes 2, 3 and 9c; this project has {', '.join(classes) or 'no recorded class'}")
    if not package["status"]["complete"]:
        raise NotApplicable("not_signed", "all three gates must be signed first (missing: " + ", ".join(package["status"]["missing"]) + ")")
    if not package["ledger"]["verified"]:
        raise NotApplicable("ledger_unverified", "the ledger does not verify; resolve that before drafting a declaration")
    ps = [x for x in pathways if x["pathway"] == "PERFORMANCE_SOLUTION"]
    accepted = [{"subject": x["subject"], "rule_id": x["rule_id"], "category": x["accepted_fail"]["category"], "reference": x["accepted_fail"]["reference"],
                 "explanation": x["accepted_fail"]["explanation"]} for x in package["accepted_fails"]]
    return {
        "banner": BANNER, "lodged": False, "kind": "NSW design compliance declaration (draft)",
        "rules_banner": package.get("banner"),
        "applies_to": {"state": "NSW", "classes": hit},
        "building": {"address": project["address"], "ncc_edition": project["ncc_edition"], "climate_zone": project["climate_zone"], "approval_date": project["approval_date"],
                     "building_parts": project["building_parts"]},
        "scope": {"discipline": "Mechanical services (HVAC)", "revision_id": package["revision"]["id"], "architect_rev": package["revision"]["architect_rev"],
                  "frozen_at": package["revision"]["frozen_at"]},
        "signed_by": [{"gate": s["gate"], "role": s["role"], "email": s["email"], "registration_no": s["registration_no"], "signed_at": s["signed_at"]} for s in package["signoffs"]],
        "independence_notice": package.get("independence_notice"),
        "results_summary": package["summary"],
        "accepted_fails": accepted,
        "performance_solutions": [{"subject": x["subject"], "rule_id": x["rule_id"], "note": x.get("note"), "evidence": x.get("evidence", [])} for x in ps],
        "fields_to_complete": [{"field": f, "note": n} for f, n in TO_COMPLETE],
        "ledger": {"anchor_seq": package["ledger"]["anchor_seq"], "anchor_hash": package["ledger"]["anchor_hash"]},
    }


def to_pdf(d: dict[str, Any]) -> bytes:
    """A deterministic PDF; the banner is on every page."""
    buf = io.BytesIO()

    def decorate(canvas: Any, doc: Any) -> None:
        canvas.saveState()
        width, height = A4
        canvas.setFillColor(colors.HexColor("#B00020"))
        canvas.setFont("Helvetica-Bold", 9)
        canvas.drawCentredString(width / 2, height - 10 * mm, d["banner"])
        canvas.setFillColor(colors.black)
        canvas.setFont("Helvetica", 7)
        canvas.drawString(15 * mm, 8 * mm, f"Revision {d['scope']['revision_id']} | ledger anchor {str(d['ledger']['anchor_hash'])[:16]} | page {doc.page}")
        canvas.restoreState()

    styles = getSampleStyleSheet()
    cell = ParagraphStyle("cell", parent=styles["BodyText"], fontSize=8, leading=10)

    def table(rows: list[list[str]], widths: list[float]) -> Table:
        t = Table([[Paragraph(escape(str(c)), cell) for c in r] for r in rows], colWidths=widths, repeatRows=1)
        t.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.25, colors.grey), ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#EEEEEE")), ("VALIGN", (0, 0), (-1, -1), "TOP")]))
        return t

    doc = SimpleDocTemplate(buf, pagesize=A4, topMargin=20 * mm, bottomMargin=14 * mm, leftMargin=15 * mm, rightMargin=15 * mm, title="NSW design compliance declaration (draft)", invariant=1)
    b, s = d["building"], d["scope"]
    parts = "; ".join(f"class {p['class']} x{p['storeys']} storeys {p['area_m2']:g} m2" for p in b["building_parts"])
    story: list[Any] = [
        Paragraph("NSW design compliance declaration (draft)", styles["Title"]),
        Paragraph(escape("Prefilled from the signed package. It contains no declaration statement and has not been lodged."), styles["Normal"]),
        Spacer(1, 4 * mm),
        Paragraph("Building", styles["Heading3"]),
        table([["Address", b["address"]], ["State / classes", f"NSW / {', '.join(d['applies_to']['classes'])}"], ["NCC edition", b["ncc_edition"]],
               ["Climate zone", str(b["climate_zone"])], ["Approval date", str(b["approval_date"])], ["Building parts", parts]], [40 * mm, 140 * mm]),
        Spacer(1, 3 * mm),
        Paragraph("Scope of this package", styles["Heading3"]),
        table([["Discipline", s["discipline"]], ["Architect revision", str(s["architect_rev"])], ["Frozen", str(s["frozen_at"])], ["Revision id", str(s["revision_id"])]], [40 * mm, 140 * mm]),
        Spacer(1, 3 * mm),
        Paragraph("Signed gates", styles["Heading3"]),
        table([["Gate", "Role", "Signed by", "Registration no.", "Signed at"]]
              + [[x["gate"], x["role"], x["email"] or "", x["registration_no"] or "(none recorded)", str(x["signed_at"])] for x in d["signed_by"]], [20 * mm, 25 * mm, 55 * mm, 40 * mm, 40 * mm]),
        Spacer(1, 3 * mm),
        Paragraph("Result summary: " + escape(", ".join(f"{k} {v}" for k, v in sorted(d["results_summary"].items())) or "none"), styles["Normal"]),
    ]
    if d["rules_banner"]:
        story += [Spacer(1, 2 * mm), Paragraph(escape(f"Rule status: {d['rules_banner']}"), styles["Normal"])]
    if d["independence_notice"]:
        story += [Paragraph(escape(f"{d['independence_notice']}: one person may have held more than one gate in this firm."), styles["Normal"])]
    story += [Spacer(1, 3 * mm), Paragraph("Accepted FAILs (for the practitioner to address)", styles["Heading3"])]
    story += [table([["System", "Rule", "Category", "Reference"]] + [[x["subject"], x["rule_id"], x["category"], x["reference"] or ""] for x in d["accepted_fails"]], [35 * mm, 70 * mm, 40 * mm, 35 * mm])
              if d["accepted_fails"] else Paragraph("None.", styles["Normal"])]
    story += [Spacer(1, 3 * mm), Paragraph("Performance solutions recorded", styles["Heading3"])]
    story += [table([["System", "Rule", "Note", "Evidence recorded"]] + [[x["subject"], x["rule_id"], x["note"] or "", "; ".join(e["title"] for e in x["evidence"]) or "none"] for x in d["performance_solutions"]],
                    [30 * mm, 55 * mm, 50 * mm, 45 * mm]) if d["performance_solutions"] else Paragraph("None recorded.", styles["Normal"])]
    story += [Spacer(1, 3 * mm), Paragraph("Fields the registered design practitioner must complete", styles["Heading3"]),
              table([["Field", "What is needed"]] + [[x["field"], x["note"]] for x in d["fields_to_complete"]], [50 * mm, 130 * mm])]
    doc.build(story, onFirstPage=decorate, onLaterPages=decorate)
    return buf.getvalue()


def validate_pdf(data: bytes, d: dict[str, Any]) -> dict[str, Any]:
    from pypdf import PdfReader

    checks: dict[str, bool] = {"pdf_header": data[:5] == b"%PDF-"}
    try:
        reader = PdfReader(io.BytesIO(data))
        pages = [" ".join((p.extract_text() or "").split()) for p in reader.pages]
    except Exception:  # noqa: BLE001 - unreadable output fails the check
        return {"passed": False, "checks": {**checks, "readable": False}}
    flat = " ".join(pages)
    checks["has_pages"] = bool(pages)
    checks["banner_on_every_page"] = all(BANNER in p for p in pages)
    checks["says_not_lodged"] = "has not been lodged" in flat
    checks["names_the_revision"] = str(d["scope"]["revision_id"]) in flat
    checks["every_signer_listed"] = all((s["registration_no"] or "(none recorded)") in flat for s in d["signed_by"])
    checks["every_field_to_complete_listed"] = all(x["field"] in flat for x in d["fields_to_complete"])
    checks["accepted_fails_listed"] = all(x["rule_id"] in flat for x in d["accepted_fails"])
    checks["performance_solutions_listed"] = all(x["rule_id"] in flat for x in d["performance_solutions"])
    checks["no_declaration_text"] = "I declare" not in flat and "hereby declare" not in flat.lower()
    return {"passed": all(checks.values()), "checks": checks, "sha256": hashlib.sha256(data).hexdigest()}
