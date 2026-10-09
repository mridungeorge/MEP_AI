"""Gate 2 / Gate 3 / ledger / share links on Postgres.

Everything that matters is decided by the security-definer functions of migration 0010 (role, gate order, frozen-before-sign, one
signer per gate, spot-check, registration number). This module calls them as the signed-in user, translates their refusals, and does
the two things that need Python: classify the stored results (mep.review.classifier) and build the signed package (mep.review.package).
"""
import hashlib
import json
import secrets
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any
from uuid import UUID

import psycopg
from psycopg.rows import dict_row

from mep.api.schedule import CurrentUser
from mep.review import package as pkg
from mep.review.classifier import classify_all


class ReviewRefused(Exception):
    """A rule of the review/sign-off state machine said no; the message is the database's own and is safe to show."""

    def __init__(self, message: str, status: int = 409) -> None:
        super().__init__(message)
        self.status = status


def _refusal(exc: psycopg.Error) -> ReviewRefused:
    text = str(exc).splitlines()[0] if str(exc) else "refused"
    forbidden = isinstance(exc, psycopg.errors.InsufficientPrivilege)
    return ReviewRefused(text, 403 if forbidden else 409)


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


class PgReview:
    def __init__(self, dsn: str, user: CurrentUser) -> None:
        self._dsn, self._user = dsn, user

    @contextmanager
    def _as_user(self) -> Iterator[psycopg.Connection[dict[str, Any]]]:
        with psycopg.connect(self._dsn, autocommit=False, row_factory=dict_row) as conn:
            conn.execute("set local role authenticated")
            conn.execute("select set_config('request.jwt.claims', %s, true)",
                         (json.dumps({"sub": str(self._user.user_id), "role": "authenticated"}),))
            yield conn

    def _service(self) -> psycopg.Connection[dict[str, Any]]:
        return psycopg.connect(self._dsn, autocommit=False, row_factory=dict_row)

    # ---- classification ---------------------------------------------------------------------------------------------
    def classify(self, revision_id: UUID) -> int:
        """Set review_class on the revision's current results (service connection: a client never writes it). Only after Gate 1 is
        signed (the revision is frozen) and until Gate 2 is signed. Idempotent: the same results always get the same class."""
        with self._service() as conn:
            rev = conn.execute("select r.parent_revision_id, r.frozen_at is not null as frozen from revision r"
                               " where r.id = %s and r.firm_id = %s", (revision_id, self._user.firm_id)).fetchone()
            if rev is None:
                raise ReviewRefused("revision not found", 404)
            if not rev["frozen"] or conn.execute("select 1 from signoff where revision_id = %s and gate = 'gate2'",
                                                 (revision_id,)).fetchone():
                return 0
            rows = conn.execute(
                "select id, subject_id, rule_id, result::text as outcome, inputs, causes, near_miss, stale from rule_result"
                " where revision_id = %s and firm_id = %s and current", (revision_id, self._user.firm_id)).fetchall()
            parents: dict[tuple[str, str], dict[str, Any]] = {}
            if rev["parent_revision_id"] is not None:
                for p in conn.execute("select subject_id, rule_id, result::text as outcome from rule_result where revision_id = %s"
                                      " and firm_id = %s and current", (rev["parent_revision_id"], self._user.firm_id)):
                    parents[(p["subject_id"], p["rule_id"])] = p
            classes = classify_all([{**r, "inputs_used": r["inputs"]} for r in rows], parents)
            n = 0
            for r in rows:
                c = classes[(r["subject_id"], r["rule_id"])]
                n += conn.execute("update rule_result set review_class = %s, review_reasons = %s::jsonb where id = %s"
                                  " and (review_class, review_reasons) is distinct from (%s, %s::jsonb)",
                                  (c.klass, json.dumps(list(c.reasons)), r["id"], c.klass, json.dumps(list(c.reasons)))).rowcount
            return n

    # ---- the Gate 2 worksheet ---------------------------------------------------------------------------------------
    def worksheet(self, revision_id: UUID) -> dict[str, Any]:
        self.classify(revision_id)
        with self._as_user() as conn:
            rev = conn.execute("select id, architect_rev, frozen_at is not null as frozen from revision where id = %s and firm_id = %s",
                               (revision_id, self._user.firm_id)).fetchone()
            if rev is None:
                raise ReviewRefused("revision not found", 404)
            rows = conn.execute(
                "select rr.id, rr.subject_id, rr.rule_id, rr.part, rr.result::text as outcome, rr.citation, rr.causes, rr.near_miss,"
                " rr.review_class, rr.review_reasons, rr.stale, rr.fix_hypotheses, rr.inputs, v.decision, v.reason, v.bulk, v.spot_check, v.user_id,"
                " v.created_at from rule_result rr left join review_latest v on v.rule_result_id = rr.id"
                " where rr.revision_id = %s and rr.firm_id = %s and rr.current order by rr.subject_id, rr.rule_id",
                (revision_id, self._user.firm_id)).fetchall()
            signoffs = conn.execute("select gate::text as gate, signer_role::text as role, signed_at, registration_no, user_id"
                                    " from signoff where revision_id = %s order by gate", (revision_id,)).fetchall()
            sample = conn.execute("select id, candidate_ids, sample_ids, created_at from review_sample where revision_id = %s"
                                  " and created_by = %s and used_at is null order by created_at desc limit 1",
                                  (revision_id, self._user.user_id)).fetchone()
        lines = []
        for r in rows:
            lines.append({
                "id": str(r["id"]), "subject_id": r["subject_id"], "rule_id": r["rule_id"], "part": r["part"], "outcome": r["outcome"],
                "citation": r["citation"], "review_class": r["review_class"], "reasons": list(r["review_reasons"] or []), "stale": r["stale"],
                "fix_hypotheses": r["fix_hypotheses"], "decision": r["decision"], "reason": r["reason"],
                "bulk": bool(r["bulk"]), "spot_check": bool(r["spot_check"]),
                "reviewed_by_me": r["user_id"] == self._user.user_id if r["decision"] else None,
                "in_sample": sample is not None and r["id"] in sample["sample_ids"]})
        by_class: dict[str, int] = {}
        for ln in lines:
            by_class[ln["review_class"] or "unclassified"] = by_class.get(ln["review_class"] or "unclassified", 0) + 1
        return {
            "revision": {"id": str(rev["id"]), "architect_rev": rev["architect_rev"], "frozen": rev["frozen"]},
            "results": lines, "by_class": by_class,
            "open_clean": sum(1 for ln in lines if ln["review_class"] == "clean_pass" and ln["decision"] is None),
            "approved": sum(1 for ln in lines if ln["decision"] == "approve"),
            "total": len(lines),
            "signoffs": [{"gate": s["gate"], "role": s["role"], "signed_at": s["signed_at"].isoformat(),
                          "registration_no": s["registration_no"], "mine": s["user_id"] == self._user.user_id} for s in signoffs],
            "sample": None if sample is None else {"id": str(sample["id"]), "size": len(sample["sample_ids"]),
                                                   "of": len(sample["candidate_ids"]),
                                                   "result_ids": [str(x) for x in sample["sample_ids"]]}}

    # ---- decisions and signatures (all through the definer functions) -----------------------------------------------
    def _call(self, sql: str, params: tuple[Any, ...]) -> Any:
        try:
            with self._as_user() as conn:
                row = conn.execute(sql, params).fetchone()
                return None if row is None else next(iter(row.values()))
        except psycopg.errors.Error as exc:
            raise _refusal(exc) from None

    def review(self, result_id: UUID, decision: str, reason: str, sample_id: UUID | None) -> int:
        return int(self._call("select gate2_review(%s, %s, %s, %s)", (result_id, decision, reason, sample_id)))

    def prepare_bulk(self, revision_id: UUID) -> str:
        self.classify(revision_id)
        return str(self._call("select gate2_prepare_bulk(%s)", (revision_id,)))

    def bulk_approve(self, sample_id: UUID) -> int:
        return int(self._call("select gate2_bulk_approve(%s)", (sample_id,)))

    def sign(self, revision_id: UUID, gate: str, registration: str | None) -> int:
        return int(self._call("select sign_gate(%s, %s::gate, %s)", (revision_id, gate, registration)))

    # ---- the ledger -------------------------------------------------------------------------------------------------
    def verify_ledger(self) -> dict[str, Any]:
        with self._as_user() as conn:
            r = conn.execute("select ok, checked, broken_seq, reason, head_seq, head_hash from verify_ledger(%s)",
                             (self._user.firm_id,)).fetchone()
        return dict(r) if r else {"ok": False}

    # ---- share links ------------------------------------------------------------------------------------------------
    def create_share(self, revision_id: UUID, days: int, label: str | None) -> dict[str, Any]:
        token = secrets.token_urlsafe(32)
        until = self._call("select create_share_link(%s, %s, %s, %s)", (revision_id, token_hash(token), days, label))
        return {"token": token, "expires_at": until.isoformat(), "days": days, "id": token_hash(token)[:12]}

    def revoke_share(self, revision_id: UUID, link_id: str) -> None:
        with self._service() as conn:      # the id is the first 12 hex digits of the stored hash: resolve it inside this revision/firm
            rows = conn.execute("select token from ledger_link where revision_id = %s and firm_id = %s and token like %s"
                                " and revoked_at is null", (revision_id, self._user.firm_id, link_id + "%")).fetchall()
        if len(rows) != 1:
            raise ReviewRefused("no such open share link", 404)
        self._call("select revoke_share_link(%s, %s)", (revision_id, rows[0]["token"]))

    def share_links(self, revision_id: UUID) -> list[dict[str, Any]]:
        with self._service() as conn:
            rows = conn.execute("select token, created_at, expires_at, revoked_at, views, last_viewed_at, label from ledger_link"
                                " where revision_id = %s and firm_id = %s order by created_at desc",
                                (revision_id, self._user.firm_id)).fetchall()
        return [{"id": r["token"][:12], "created_at": r["created_at"].isoformat(), "expires_at": r["expires_at"].isoformat(),
                 "revoked": r["revoked_at"] is not None, "views": r["views"], "label": r["label"],
                 "last_viewed_at": r["last_viewed_at"].isoformat() if r["last_viewed_at"] else None} for r in rows]

    # ---- the signed package -----------------------------------------------------------------------------------------
    def package(self, revision_id: UUID) -> dict[str, Any]:
        with self._service() as conn:
            out = pkg.assemble(conn, self._user.firm_id, revision_id)
        if out is None:
            raise ReviewRefused("revision not found", 404)
        return out

    def record_artifact(self, revision_id: UUID, data: bytes, validator: dict[str, Any], complete: bool) -> bool:
        """Record the generated PDF (checksum + validator result) once. It is 'released' only when the validator passed AND all three
        gates are signed. Service connection: a client never writes an artifact."""
        checksum = hashlib.sha256(data).hexdigest()
        with self._service() as conn:
            if conn.execute("select 1 from artifact where revision_id = %s and firm_id = %s and checksum = %s",
                            (revision_id, self._user.firm_id, checksum)).fetchone():
                return False
            released = bool(validator.get("passed")) and complete
            conn.execute("insert into artifact (firm_id, revision_id, kind, path, checksum, validator, released)"
                         " values (%s, %s, 'compliance_report_pdf', %s, %s, %s::jsonb, %s)",
                         (self._user.firm_id, revision_id, f"generated/{revision_id}/{checksum}.pdf", checksum,
                          json.dumps(validator), released))
        return True


class PgShare:
    """The public, read-only door: a token opens exactly one revision's package, for a limited time, and every opening is logged."""

    def __init__(self, dsn: str) -> None:
        self._dsn = dsn

    def open(self, token: str, client: str | None) -> dict[str, Any] | None:
        """The package for the token, cut down to what a certifier needs (no firm-wide ledger counts, no reviewer addresses)."""
        out = self._open(token, client)
        if out is not None:
            for k in ("events", "head_seq", "head_hash"):
                out["ledger"].pop(k, None)
            for r in out["results"]:
                r.pop("reviewed_by", None)
        return out

    def _open(self, token: str, client: str | None) -> dict[str, Any] | None:
        if not token or len(token) > 200:
            return None
        with psycopg.connect(self._dsn, autocommit=False, row_factory=dict_row) as conn:
            row = conn.execute("select firm_id, revision_id from open_share_link(%s, %s)", (token_hash(token), client)).fetchone()
            if row is None:
                return None
            return pkg.assemble(conn, row["firm_id"], row["revision_id"])
