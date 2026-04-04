# Copyright 2026 Andrei Patsiomkin
# SPDX-License-Identifier: Apache-2.0

"""
d1_client.py — Cloudflare D1 HTTP API wrapper.

Reads credentials from environment:
  CF_D1_TOKEN        — Cloudflare API token with D1:Edit permission
  CF_ACCOUNT_ID      — Cloudflare account ID  (default: hardcoded)
  CF_D1_DATABASE_ID  — D1 database ID         (default: hardcoded)
"""
from __future__ import annotations

import os
import sys
import time
import requests

ACCOUNT_ID  = os.environ.get("CF_ACCOUNT_ID",     "bab29d78c0a1173324d4213f42103f01")
DATABASE_ID = os.environ.get("CF_D1_DATABASE_ID", "52210bd9-a019-415e-8f12-6a73b42278f9")
_BASE = (
    f"https://api.cloudflare.com/client/v4/accounts/{ACCOUNT_ID}"
    f"/d1/database/{DATABASE_ID}"
)

_TOKEN: str = ""


def configure(token: str) -> None:
    global _TOKEN
    _TOKEN = token


def _headers() -> dict:
    if not _TOKEN:
        sys.exit("CF_D1_TOKEN not set — export it or pass --token-file")
    return {"Authorization": f"Bearer {_TOKEN}", "Content-Type": "application/json"}


def query(sql: str, params: list | None = None) -> list:
    """Execute a single SQL statement, return result rows."""
    body: dict = {"sql": sql}
    if params is not None:
        body["params"] = params
    r = requests.post(f"{_BASE}/query", headers=_headers(), json=body, timeout=30)
    r.raise_for_status()
    d = r.json()
    if not d.get("success"):
        raise RuntimeError(f"D1 query failed: {d.get('errors')}")
    return (d.get("result") or [{}])[0].get("results", [])


def batch(statements: list[dict], retries: int = 4) -> None:
    """POST a batch of {sql, params} dicts to /batch endpoint."""
    for attempt in range(retries):
        r = requests.post(
            f"{_BASE}/batch", headers=_headers(), json=statements, timeout=90
        )
        if r.status_code == 429:
            wait = int(r.headers.get("Retry-After", 60))
            print(f"\n  Rate-limited - waiting {wait}s ...", flush=True)
            time.sleep(wait)
            continue
        r.raise_for_status()
        d = r.json()
        if not d.get("success"):
            raise RuntimeError(f"D1 batch failed: {d.get('errors')}")
        return
    raise RuntimeError("D1 batch: too many retries")


def batch_upsert(
    sql: str,
    rows: list[list],
    chunk: int = 100,
    label: str = "",
) -> int:
    """
    Batch-insert rows using the given INSERT OR REPLACE … statement.
    Returns number of rows sent.
    """
    n = len(rows)
    if n == 0:
        print(f"  {label}: 0 rows — skipped")
        return 0
    for i in range(0, n, chunk):
        block = rows[i : i + chunk]
        stmts = [{"sql": sql, "params": row} for row in block]
        batch(stmts)
        done = min(i + chunk, n)
        print(f"\r  {label}: {done}/{n}", end="", flush=True)
    print(f"\r  {label}: {n}/{n} done    ")
    return n


def apply_schema(schema_path: str) -> None:
    """Execute every non-empty statement in a .sql file."""
    sql = open(schema_path, encoding="utf-8").read()
    # Strip comment lines before splitting on ;
    clean_lines = [l for l in sql.splitlines() if not l.strip().startswith("--")]
    clean_sql = "\n".join(clean_lines)
    statements = [s.strip() for s in clean_sql.split(";") if s.strip()]
    for stmt in statements:
        query(stmt)
    print(f"  Schema applied ({len(statements)} statements)")
