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
import logging

# Configure logging
logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

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
    logger.info("D1 client configured with token (length: %d)", len(token) if token else 0)

def _headers() -> dict:
    if not _TOKEN:
        sys.exit("CF_D1_TOKEN not set — export it or pass --token-file")
    return {"Authorization": f"Bearer {_TOKEN}", "Content-Type": "application/json"}

def query(sql: str, params: list | None = None) -> list:
    """Execute a single SQL statement, return result rows."""
    body: dict = {"sql": sql}
    if params is not None:
        body["params"] = params
    
    logger.debug("Executing query: %s", sql)
    if params:
        logger.debug("Query params: %s", params)
    
    try:
        r = requests.post(f"{_BASE}/query", headers=_headers(), json=body, timeout=30)
        
        if not r.ok:
            # Capture comprehensive error details
            error_msg = f"D1 Query failed with status {r.status_code}"
            logger.error(error_msg)
            logger.error("Request URL: %s", r.url)
            logger.error("Request body: %s", body)
            
            try:
                error_response = r.json()
                logger.error("Error response: %s", error_response)
            except Exception as json_err:
                logger.error("Could not parse error response as JSON: %s", r.text[:500])
            
            r.raise_for_status()
        
        d = r.json()
        if not d.get("success"):
            error_details = d.get('errors', 'Unknown error')
            logger.error("D1 query failed (success=false): %s", error_details)
            raise RuntimeError(f"D1 query failed: {error_details}")
        
        logger.debug("Query executed successfully")
        return (d.get("result") or [{}])[0].get("results", [])
    except requests.exceptions.HTTPError as http_err:
        logger.error("HTTP error in query(): %s", http_err)
        raise
    except Exception as err:
        logger.error("Unexpected error in query(): %s", err)
        raise


_D1_MAX_VARS = 90  # D1 caps at 100 bindings per statement; stay under

# Max bytes per /raw SQL statement (D1 hard limit ~100 KB for statement body)
_RAW_CHUNK_BYTES = 90_000


def _sql_val(v) -> str:
    """Escape a Python value to a safe SQL literal."""
    if v is None:
        return "NULL"
    if isinstance(v, bool):
        return "1" if v else "0"
    if isinstance(v, (int, float)):
        return repr(v)
    # String: escape single quotes by doubling, wrap in single quotes
    return "'" + str(v).replace("'", "''") + "'"

def raw_upsert(
    base_sql: str,
    rows: list[list],
    label: str = "",
) -> int:
    """
    Bulk-insert using /raw endpoint with embedded SQL literals.

    No binding limit — sends up to ~800 KB of SQL per request.
    base_sql must be everything up to and including "VALUES".
    Example: "INSERT OR REPLACE INTO t (a,b,c) VALUES"
    Returns number of rows sent.
    """
    n = len(rows)
    if n == 0:
        print(f"  {label}: 0 rows - skipped")
        return 0

    chunk: list[str] = []
    chunk_bytes = 0
    sent = 0

    def _flush(chunk_rows: list[str], up_to: int) -> None:
        sql = base_sql + " " + ",".join(chunk_rows)
        _raw_with_retry(sql)
        print(f"\r  {label}: {up_to}/{n}", end="", flush=True)

    for i, row in enumerate(rows):
        row_sql = "(" + ",".join(_sql_val(v) for v in row) + ")"
        row_bytes = len(row_sql.encode("utf-8"))
        if chunk and chunk_bytes + row_bytes + 1 > _RAW_CHUNK_BYTES:
            _flush(chunk, sent)
            chunk = []
            chunk_bytes = 0
        chunk.append(row_sql)
        chunk_bytes += row_bytes + 1
        sent += 1

    if chunk:
        _flush(chunk, sent)

    print(f"\r  {label}: {n}/{n} done    ")
    return n


def _raw_with_retry(sql: str, retries: int = 5) -> list:
    """POST raw SQL to /raw endpoint with retry on 429."""
    body: dict = {"sql": sql}
    
    logger.debug("Executing raw SQL (length: %d chars)", len(sql))
    
    for attempt in range(retries):
        try:
            r = requests.post(f"{_BASE}/raw", headers=_headers(), json=body, timeout=120)
            
            if r.status_code == 429:
                wait = int(r.headers.get("Retry-After", 30))
                print(f"\n  Rate-limited - waiting {wait}s ...", flush=True)
                logger.warning("Rate limited by Cloudflare, waiting %d seconds", wait)
                time.sleep(wait)
                continue
            
            if not r.ok:
                # Print error body for debugging
                logger.error("Raw query failed with status %d on attempt %d/%d", r.status_code, attempt + 1, retries)
                logger.error("Request URL: %s", r.url)
                logger.error("SQL (first 500 chars): %s", sql[:500])
                
                try:
                    err = r.json().get("errors", r.text[:200])
                except Exception:
                    err = r.text[:200]
                
                logger.error("Error details: %s", err)
                raise RuntimeError(f"D1 raw {r.status_code}: {err}")
            
            d = r.json()
            if not d.get("success"):
                error_msg = d.get("errors", "Unknown error")
                logger.error("D1 raw failed (success=false): %s", error_msg)
                raise RuntimeError(f"D1 raw failed: {error_msg}")
            
            logger.debug("Raw query executed successfully")
            return (d.get("result") or [{}])[0].get("results", [])
            
        except requests.exceptions.RequestException as req_err:
            logger.error("Request error on attempt %d/%d: %s", attempt + 1, retries, req_err)
            if attempt == retries - 1:
                raise
            time.sleep(2 ** attempt)  # Exponential backoff
    
    raise RuntimeError("D1 raw: too many retries")

def batch_upsert(
    sql: str,
    rows: list[list],
    chunk: int = 0,
    label: str = "",
) -> int:
    """
    Batch-insert using parameterized /query (for small tables, CI sync).

    For large bulk imports use raw_upsert() instead.
    D1 caps at 100 bindings per statement, chunk is auto-sized.
    """
    n = len(rows)
    if n == 0:
        print(f"  {label}: 0 rows - skipped")
        return 0

    row_width = len(rows[0])
    effective_chunk = chunk if chunk > 0 else max(1, _D1_MAX_VARS // row_width)
    
    logger.debug("Batch upsert: %d rows, effective chunk size: %d", n, effective_chunk)

    sql_upper = sql.upper()
    val_idx = sql_upper.rfind(" VALUES ")
    if val_idx == -1:
        logger.error("Cannot find VALUES in SQL: %s", sql)
        raise ValueError(f"Cannot find VALUES in sql: {sql!r}")
    
    base   = sql[:val_idx]
    one_ph = sql[val_idx + len(" VALUES "):]

    for i in range(0, n, effective_chunk):
        block = rows[i : i + effective_chunk]
        multi_ph = ",".join(one_ph for _ in block)
        flat_params = [v for row in block for v in row]
        multi_sql = f"{base} VALUES {multi_ph}"
        _query_with_retry(multi_sql, flat_params)
        done = min(i + effective_chunk, n)
        print(f"\r  {label}: {done}/{n}", end="", flush=True)

    print(f"\r  {label}: {n}/{n} done    ")
    return n

def _query_with_retry(sql: str, params: list, retries: int = 5) -> list:
    """POST to /query with retry on 429 rate-limit."""
    body: dict = {"sql": sql, "params": params}
    
    logger.debug("Query with retry (sql length: %d, params: %d)", len(sql), len(params) if params else 0)    
    for attempt in range(retries):
        try:
            r = requests.post(f"{_BASE}/query", headers=_headers(), json=body, timeout=60)
            
            if r.status_code == 429:
                wait = int(r.headers.get("Retry-After", 30))
                print(f"\n  Rate-limited - waiting {wait}s ...", flush=True)
                logger.warning("Rate limited on attempt %d/%d, waiting %d seconds", attempt + 1, retries, wait)
                time.sleep(wait)
                continue
            
            if not r.ok:
                logger.error("Query failed with status %d on attempt %d/%d", r.status_code, attempt + 1, retries)
                logger.error("Request URL: %s", r.url)
                logger.error("SQL (first 500 chars): %s", sql[:500])
                logger.error("Number of params: %d", len(params) if params else 0)
                
                try:
                    error_response = r.json()
                    logger.error("Error response: %s", error_response)
                except Exception:
                    logger.error("Response text (first 500 chars): %s", r.text[:500])
            
            r.raise_for_status()
            
            d = r.json()
            if not d.get("success"):
                error_msg = d.get("errors", "Unknown error")
                logger.error("D1 query failed (success=false): %s", error_msg)
                raise RuntimeError(f"D1 query failed: {error_msg}")
            
            logger.debug("Query with retry executed successfully")
            return (d.get("result") or [{}])[0].get("results", [])
            
        except requests.exceptions.RequestException as req_err:
            logger.error("Request exception on attempt %d/%d: %s", attempt + 1, retries, req_err)
            if attempt < retries - 1:
                time.sleep(2 ** attempt)  # Exponential backoff
            else:
                raise
    
    raise RuntimeError("D1 query: too many retries")

def apply_schema(schema_path: str) -> None:
    """Execute every non-empty statement in a .sql file."""
    logger.info("Applying schema from: %s", schema_path)
    
    sql = open(schema_path, encoding="utf-8").read()
    # Strip comment lines before splitting on ;
    clean_lines = [l for l in sql.splitlines() if not l.strip().startswith("--")]
    clean_sql = "\n".join(clean_lines)
    statements = [s.strip() for s in clean_sql.split(";") if s.strip()]
    
    logger.info("Found %d SQL statements in schema file", len(statements))
    
    for idx, stmt in enumerate(statements, 1):
        try:
            logger.debug("Executing schema statement %d/%d (length: %d chars)", idx, len(statements), len(stmt))
            logger.debug("Statement preview: %s", stmt[:100])
            query(stmt)
        except Exception as err:
            logger.error("Failed to execute schema statement %d/%d", idx, len(statements))
            logger.error("Statement (first 200 chars): %s", stmt[:200])
            logger.error("Error: %s", err)
            raise
    
    logger.info("Schema applied successfully (%d statements)", len(statements))
    print(f"  Schema applied ({len(statements)} statements)")
