"""The signed compliance package: one read-only snapshot of a revision (results, reviews, sign-offs, ledger check) and its PDF.

`assemble` only READS stored rows (on a service connection the caller scopes to one firm and revision); nothing here decides compliance
or approves a rule. The PDF is deterministic for the same data (no timestamps of its own), so its checksum is stable and the
artifact row that records it can be checked later. The DRAFT banner is on every page while any cited rule is still a draft.
"""
import hashlib
import io
from typing import Any
from uuid import UUID
from xml.sax.saxutils import escape

import psycopg
from psycopg.rows import dict_row
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

PACKAGE_VERSION = 1
DRAFT_BANNER = "DRAFT RULES: NOT ENGINEER-APPROVED"
GATES = ("gate1", "gate2", "gate3")


def _json(v: Any) -> Any:
    if isinstance(v, UUID):
        return str(v)
    if hasattr(v, "isoformat"):
        return v.isoformat()
    if isinstance(v, dict):
        return {k: _json(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_json(x) for x in v]
    return v


def assemble(conn: psycopg.Connection[Any], firm_id: UUID, revision_id: UUID) -> dict[str, Any] | None:
    """The package for one revision, or None when it is not in that firm."""
    cur = conn.cursor(row_factory=dict_row)
    rev = cur.execute(
        "select r.id, r.architect_rev, r.status, r.frozen_at, r.parent_revision_id, p.address, p.state, p.climate_zone,"
        " p.ncc_edition, p.approval_date from revision r join project p on p.id = r.project_id and p.firm_id = r.firm_id"
        " where r.id = %s and r.firm_id = %s", (revision_id, firm_id)).fetchone()
    if rev is None:
        return None
    parts = cur.execute("select building_class, storeys, area_m2_value from building_part b join revision r on r.project_id = b.project_id"
                        " and r.firm_id = b.firm_id where r.id = %s order by b.position", (revision_id,)).fetchall()
    ancestors = cur.execute(
        "with recursive up as (select id, parent_revision_id, architect_rev, 1 as depth from revision where id = %s and firm_id = %s"
        " union all select r.id, r.parent_revision_id, r.architect_rev, up.depth + 1 from revision r join up on r.id = up.parent_revision_id"
        " where r.firm_id = %s and up.depth < 50) select architect_rev from up where depth > 1 order by depth",
        (revision_id, firm_id, firm_id)).fetchall()
    results = cur.execute(
        "select rr.id, rr.subject_id, rr.rule_id, rr.part, rr.result::text as outcome, rr.citation, rr.causes, rr.near_miss,"
        " rr.fix_hypotheses, rr.review_class, rr.review_reasons, rr.stale, rr.inputs, v.decision, v.reason, v.bulk, v.spot_check, v.created_at as reviewed_at,"
        " u.email as reviewer from rule_result rr left join review_latest v on v.rule_result_id = rr.id"
        " left join auth.users u on u.id = v.user_id where rr.revision_id = %s and rr.firm_id = %s and rr.current"
        " order by rr.subject_id, rr.rule_id", (revision_id, firm_id)).fetchall()
    signoffs = cur.execute(
        "select s.gate::text as gate, s.signed_at, s.signer_role::text as role, s.registration_no, s.anchor_seq, s.anchor_hash, s.statement, u.email"
        " from signoff s left join auth.users u on u.id = s.user_id where s.revision_id = %s and s.firm_id = %s order by s.gate",
        (revision_id, firm_id)).fetchall()
    ledger = cur.execute("select ok, checked, broken_seq, reason, head_seq, head_hash from verify_ledger(%s)", (firm_id,)).fetchone()
    if ledger is None:
        raise RuntimeError("verify_ledger returned nothing")
    # The PDF shows the ledger position fixed at the LAST sign-off (so it does not change every time the package is opened or the PDF
    # is recorded); the live head is in the JSON.
    anchored = [s for s in signoffs if s["anchor_seq"] is not None]
    anchor = anchored[-1] if anchored else None
    drafts = sorted({r["citation"].get("rule_status", "draft") for r in results if isinstance(r["citation"], dict)})
    signed = {s["gate"] for s in signoffs}
    lines = []
    for r in results:
        lines.append({
            "subject": r["subject_id"], "rule_id": r["rule_id"], "part": r["part"], "outcome": r["outcome"],
            "citation": r["citation"], "review_class": r["review_class"], "reasons": list(r["review_reasons"] or []), "stale": r["stale"],
            "decision": r["decision"], "reason": r["reason"], "bulk": bool(r["bulk"]), "spot_check": bool(r["spot_check"]),
            "reviewed_by": r["reviewer"], "reviewed_at": r["reviewed_at"], "fix_hypotheses": r["fix_hypotheses"]})
    counts: dict[str, int] = {}
    for line in lines:
        counts[line["outcome"]] = counts.get(line["outcome"], 0) + 1
    out = {
        "package_version": PACKAGE_VERSION,
        "banner": DRAFT_BANNER if (not drafts or any(d != "approved" for d in drafts)) else None,
        "revision": {"id": rev["id"], "architect_rev": rev["architect_rev"], "status": rev["status"], "frozen_at": rev["frozen_at"],
                     "derived_from": [a["architect_rev"] for a in ancestors]},
        "project": {"address": rev["address"], "state": rev["state"], "climate_zone": rev["climate_zone"],
                    "ncc_edition": rev["ncc_edition"], "approval_date": rev["approval_date"],
                    "building_parts": [{"class": p["building_class"], "storeys": p["storeys"], "area_m2": float(p["area_m2_value"] or 0)}
                                       for p in parts]},
        "summary": counts,
        "results": lines,
        "signoffs": [{"gate": s["gate"], "role": s["role"], "email": s["email"], "signed_at": s["signed_at"],
                      "registration_no": s["registration_no"], "ledger_anchor_seq": s["anchor_seq"],
                      "attests": (s["statement"] or {}).get("attests")} for s in signoffs],
        "status": {"signed_gates": [g for g in GATES if g in signed],
                   "complete": all(g in signed for g in GATES),
                   "missing": [g for g in GATES if g not in signed]},
        "ledger": {"verified": bool(ledger["ok"]), "events": ledger["checked"], "broken_at": ledger["broken_seq"],
                   "reason": ledger["reason"], "head_seq": ledger["head_seq"], "head_hash": ledger["head_hash"],
                   "anchor_seq": None if anchor is None else anchor["anchor_seq"],
                   "anchor_hash": None if anchor is None else anchor["anchor_hash"]},
    }
    return _json(out)  # type: ignore[no-any-return]


def status_line(pkg: dict[str, Any]) -> str:
    st = pkg["status"]
    if st["complete"] and not pkg["ledger"]["verified"]:
        return "NOT VERIFIED: the audit ledger hash chain does not verify"
    if st["complete"]:
        return "SIGNED: Gate 1 (designer), Gate 2 (checker) and Gate 3 (approver)"
    return "NOT FULLY SIGNED: missing " + ", ".join(g.replace("gate", "Gate ") for g in st["missing"])


def to_pdf(pkg: dict[str, Any]) -> bytes:
    """Deterministic PDF of the package."""
    buf = io.BytesIO()
    banner = pkg.get("banner")
    status = status_line(pkg)
    ok = pkg["status"]["complete"] and pkg["ledger"]["verified"]

    def decorate(canvas: Any, doc: Any) -> None:
        canvas.saveState()
        width, height = landscape(A4)
        if banner:
            canvas.setFillColor(colors.HexColor("#B00020"))
            canvas.setFont("Helvetica-Bold", 12)
            canvas.drawCentredString(width / 2, height - 9 * mm, banner)
        canvas.setFillColor(colors.HexColor("#1B5E20") if ok else colors.HexColor("#B00020"))
        canvas.setFont("Helvetica-Bold", 8)
        canvas.drawCentredString(width / 2, height - 14 * mm, status)
        canvas.setFillColor(colors.black)
        canvas.setFont("Helvetica", 7)
        led = pkg["ledger"]
        canvas.drawString(12 * mm, 8 * mm, f"Revision {pkg['revision']['id']}  |  ledger anchor {led['anchor_seq']} {str(led['anchor_hash'])[:16]}  |  page {doc.page}")
        canvas.restoreState()

    styles = getSampleStyleSheet()
    cell = ParagraphStyle("cell", parent=styles["BodyText"], fontSize=7, leading=9)
    doc = SimpleDocTemplate(buf, pagesize=landscape(A4), topMargin=20 * mm, bottomMargin=14 * mm, leftMargin=12 * mm,
                            rightMargin=12 * mm, title="Compliance package", invariant=1)
    p, rv = pkg["project"], pkg["revision"]
    parts = "; ".join(f"class {x['class']} x{x['storeys']} storeys {x['area_m2']:g} m2" for x in p["building_parts"]) or "none recorded"
    story: list[Any] = [
        Paragraph("Compliance package", styles["Title"]),
        Paragraph(escape(f"{p['address']} | {p['ncc_edition']} | {p['state']} | climate zone {p['climate_zone']} | approval date "
                         f"{p['approval_date']} | {parts}"), styles["Normal"]),
        Paragraph(escape(f"Architect revision {rv['architect_rev']}"
                         + (f" (derived from {' > '.join(reversed(rv['derived_from']))})" if rv["derived_from"] else "")
                         + f" | status {rv['status']} | frozen {rv['frozen_at']}"), styles["Normal"]),
        Paragraph("Summary: " + ", ".join(f"{k} {v}" for k, v in sorted(pkg["summary"].items())), styles["Normal"]),
        Spacer(1, 3 * mm), Paragraph("Sign-offs", styles["Heading3"]),
    ]
    rows: list[list[Any]] = [[Paragraph(h, cell) for h in ("Gate", "Role", "Signer", "Registration no.", "Signed at (UTC)", "Attests")]]
    for s in pkg["signoffs"]:
        rows.append([Paragraph(escape(str(v or "-")), cell) for v in (
            s["gate"].replace("gate", "Gate "), s["role"], s["email"], s["registration_no"], s["signed_at"], s["attests"])])
    if len(rows) == 1:
        rows.append([Paragraph("none", cell)] + [Paragraph("-", cell)] * 5)
    sign_table = Table(rows, repeatRows=1, colWidths=[18 * mm, 22 * mm, 60 * mm, 36 * mm, 50 * mm, 80 * mm])
    sign_table.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.25, colors.grey), ("BACKGROUND", (0, 0), (-1, 0), colors.lightgrey)]))
    story += [sign_table, Spacer(1, 3 * mm), Paragraph("Results and review", styles["Heading3"])]
    rows = [[Paragraph(h, cell) for h in ("Subject", "Rule / clause", "Outcome", "Class", "Review decision and reason")]]
    for r in pkg["results"]:
        c = r["citation"]
        how = "not reviewed" if r["decision"] is None else (
            f"{'accepted FAIL' if (r['decision'] == 'approve' and r['outcome'] == 'FAIL') else r['decision']}"
            f"{' (bulk, after spot-check)' if r['bulk'] else ''}: {r['reason']} | {r.get('reviewed_by') or ''}")
        extra = ("; ".join(r["reasons"]) + " | ") if r["reasons"] else ""
        rows.append([Paragraph(escape(str(v)), cell) for v in (
            r["subject"] + (f" (part {r['part'] + 1})" if r["part"] is not None else ""),
            f"{r['rule_id']} - {c.get('document', '')} {c.get('clause', '')} ({c.get('rule_status', '')})", r["outcome"],
            r["review_class"] or "-", extra + how)])
    table = Table(rows, repeatRows=1, colWidths=[30 * mm, 80 * mm, 24 * mm, 26 * mm, 105 * mm])
    table.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.25, colors.grey), ("BACKGROUND", (0, 0), (-1, 0), colors.lightgrey),
                               ("VALIGN", (0, 0), (-1, -1), "TOP")]))
    led = pkg["ledger"]
    story += [table, Spacer(1, 3 * mm), Paragraph("Audit ledger", styles["Heading3"]),
              Paragraph(escape(f"Hash chain {'VERIFIED' if led['verified'] else 'NOT VERIFIED: ' + str(led['reason'])} when this package was made; "
                               f"anchored at the last sign-off: event {led['anchor_seq']} {led['anchor_hash']}"), styles["Normal"])]
    doc.build(story, onFirstPage=decorate, onLaterPages=decorate)
    return buf.getvalue()


def validate_pdf(data: bytes, pkg: dict[str, Any]) -> dict[str, Any]:
    """Checks the generated PDF against the package it came from. Recorded with the artifact; the artifact is released only if it passed."""
    from pypdf import PdfReader
    checks: dict[str, bool] = {"pdf_header": data[:5] == b"%PDF-"}
    try:
        reader = PdfReader(io.BytesIO(data))
        text = "\n".join((page.extract_text() or "") for page in reader.pages)
        checks["has_pages"] = len(reader.pages) > 0
    except Exception:  # noqa: BLE001 - unreadable output simply fails the check
        return {"passed": False, "checks": {**checks, "readable": False}}
    flat = " ".join(text.split())
    checks["names_the_revision"] = str(pkg["revision"]["id"]) in flat
    checks["shows_sign_off_status"] = status_line(pkg) in flat
    checks["shows_ledger_anchor"] = str(pkg["ledger"]["anchor_hash"])[:16] in flat
    checks["every_result_listed"] = all(r["rule_id"] in flat for r in pkg["results"])
    checks["banner_when_draft"] = (not pkg.get("banner")) or DRAFT_BANNER in flat
    checks["registration_shown"] = all((s["registration_no"] or "") in flat for s in pkg["signoffs"])
    return {"passed": all(checks.values()), "checks": checks, "sha256": hashlib.sha256(data).hexdigest()}
