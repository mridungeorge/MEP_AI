"""Ledger anchoring: export each firm's signed-ledger head hash to a separate, write-once store, and verify the database chain against the anchors.

    ledger_anchor.py export --dsn $MEP_DB_URL --store anchors/                    (one anchor file per firm head that has not been anchored yet)
    ledger_anchor.py verify --dsn $MEP_DB_URL --store anchors/                    (exit 1 and say what broke if the database no longer matches its anchors)

The database's hash chain proves the rows are consistent with each other. It cannot prove that someone with database access did not rewrite the chain AND its head
from the start. An anchor is a copy of the head (firm, seq, row hash) kept somewhere the database administrator cannot quietly change: the store. Each anchor file
also records the SHA-256 of the firm's previous anchor file, so removing or editing an anchor breaks the anchor chain as well.

The store here is a directory. Files are created with O_EXCL (never overwritten) and made read-only. For real tamper resistance point `--store` at a mount whose
storage enforces write-once (an object-lock bucket mounted or synced to the directory): the bucket wiring needs credentials and is not part of this script.
"""
import argparse
import hashlib
import json
import os
import stat
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import psycopg
from psycopg.rows import dict_row

GENESIS = "0" * 64


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def anchor_files(store: Path, firm: str) -> list[Path]:
    folder = store / firm
    return sorted(folder.glob("*.json"), key=lambda p: int(p.name.split("-", 1)[0])) if folder.is_dir() else []


def read_anchor(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))  # type: ignore[no-any-return]


def write_once(path: Path, data: bytes) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o444)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
    finally:
        os.chmod(path, stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)


def export_anchors(dsn: str, store: Path, now: datetime | None = None) -> list[str]:
    """Anchor every firm whose head has moved since its last anchor. Returns the files written. A firm whose ledger does not verify is NOT anchored (that is reported)."""
    written: list[str] = []
    stamp = (now or datetime.now(UTC)).strftime("%Y-%m-%dT%H:%M:%SZ")
    with psycopg.connect(dsn, autocommit=True, row_factory=dict_row) as conn:
        for h in conn.execute("select firm_id, seq, row_hash from ledger_head order by firm_id").fetchall():
            firm = str(h["firm_id"])
            verdict = conn.execute("select ok, reason from verify_ledger(%s)", (h["firm_id"],)).fetchone()
            if verdict is None or not verdict["ok"]:
                print(f"NOT ANCHORED {firm}: the ledger does not verify ({verdict['reason'] if verdict else 'no verdict'})", file=sys.stderr)
                continue
            existing = anchor_files(store, firm)
            last = read_anchor(existing[-1]) if existing else None
            if last is not None and last["seq"] >= h["seq"]:
                continue
            prev_file_hash = _sha(existing[-1].read_bytes()) if existing else GENESIS
            record = {"format": 1, "firm_id": firm, "seq": int(h["seq"]), "row_hash": h["row_hash"], "previous_anchor_sha256": prev_file_hash, "exported_at": stamp}
            body = (json.dumps(record, sort_keys=True, indent=1) + "\n").encode()
            folder = store / firm
            folder.mkdir(parents=True, exist_ok=True)
            path = folder / f"{record['seq']}-{record['row_hash'][:16]}.json"
            write_once(path, body)
            written.append(str(path))
    return written


def verify_anchors(dsn: str, store: Path) -> list[str]:
    """Problems found (empty = the database matches every anchor and the anchor chains are intact)."""
    problems: list[str] = []
    with psycopg.connect(dsn, autocommit=True, row_factory=dict_row) as conn:
        firms = [str(r["firm_id"]) for r in conn.execute("select firm_id from ledger_head").fetchall()]
        for firm in firms:
            files = anchor_files(store, firm)
            if not files:
                problems.append(f"{firm}: no anchor in the store")
                continue
            verdict = conn.execute("select ok, reason from verify_ledger(%s)", (firm,)).fetchone()
            if verdict is None or not verdict["ok"]:
                problems.append(f"{firm}: the database chain does not verify ({verdict['reason'] if verdict else 'no verdict'})")
            prev_hash, prev_seq = GENESIS, 0
            for p in files:
                raw = p.read_bytes()
                try:
                    a = json.loads(raw)
                    assert a["firm_id"] == firm and isinstance(a["seq"], int)
                except (ValueError, KeyError, AssertionError):
                    problems.append(f"{firm}: {p.name} is not a valid anchor")
                    prev_hash = _sha(raw)
                    continue
                if a["previous_anchor_sha256"] != prev_hash:
                    problems.append(f"{firm}: {p.name} does not follow the anchor before it (an anchor was edited or removed)")
                if a["seq"] <= prev_seq:
                    problems.append(f"{firm}: {p.name} goes backwards")
                row = conn.execute("select row_hash from ledger_event where firm_id = %s and seq = %s", (firm, a["seq"])).fetchone()
                if row is None:
                    problems.append(f"{firm}: the database has no ledger row {a['seq']} that {p.name} anchored (rows removed?)")
                elif row["row_hash"] != a["row_hash"]:
                    problems.append(f"{firm}: ledger row {a['seq']} no longer has the hash anchored in {p.name} (rows rewritten)")
                prev_hash, prev_seq = _sha(raw), a["seq"]
    return problems


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("export", "verify"):
        p = sub.add_parser(name)
        p.add_argument("--dsn", default=os.environ.get("MEP_DB_URL"), required=os.environ.get("MEP_DB_URL") is None)
        p.add_argument("--store", required=True, type=Path)
    a = ap.parse_args()
    if a.cmd == "export":
        written = export_anchors(a.dsn, a.store)
        print(f"anchored {len(written)} firm head(s)")
        return 0
    problems = verify_anchors(a.dsn, a.store)
    for line in problems:
        print("PROBLEM", line)
    print("anchors verified" if not problems else f"{len(problems)} problem(s)")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
