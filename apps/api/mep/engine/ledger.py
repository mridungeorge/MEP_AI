"""Postgres writer for the append-only ledger (service role). Used for jurisdiction overrides."""
import json
from typing import Any

import psycopg
from psycopg.pq import TransactionStatus


class PostgresLedger:
    """Writes `ledger_event` rows through a service-role connection; the table is append-only in the DB."""

    def __init__(self, conn: psycopg.Connection[Any]) -> None:
        self._conn = conn

    def write(self, kind: str, payload: dict[str, Any], *, firm_id: str, revision_id: str) -> None:
        # A savepoint inside a caller's open transaction is not a durable commit: refuse (fail closed).
        if self._conn.info.transaction_status != TransactionStatus.IDLE:
            raise RuntimeError("the ledger connection must have no open transaction (the write must commit now)")
        with self._conn.transaction():
            self._conn.execute(
                "insert into ledger_event (firm_id, revision_id, kind, payload) values (%s, %s, %s, %s::jsonb)",
                (firm_id, revision_id or None, kind, json.dumps(payload)),
            )
