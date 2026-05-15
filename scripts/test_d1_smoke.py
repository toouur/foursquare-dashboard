"""
Smoke-test the D1 schema: verify required tables exist and have rows.
Exit 1 if any check fails.
"""
import os
import sys
import json
import urllib.request
import urllib.error

ACCOUNT_ID  = os.environ["CF_ACCOUNT_ID"]
DATABASE_ID = os.environ["CF_D1_DATABASE_ID"]
TOKEN       = os.environ["CF_D1_TOKEN"]

API = f"https://api.cloudflare.com/client/v4/accounts/{ACCOUNT_ID}/d1/database/{DATABASE_ID}/query"

CHECKS = [
    ("checkins",  "SELECT COUNT(*) AS n FROM checkins",  100),
    ("venues",    "SELECT COUNT(*) AS n FROM venues",    100),
    ("tips",      "SELECT COUNT(*) AS n FROM tips",        0),
    ("ratings",   "SELECT COUNT(*) AS n FROM ratings",     0),
]

def query(sql: str) -> list[dict]:
    body = json.dumps({"sql": sql}).encode()
    req  = urllib.request.Request(API, data=body, method="POST",
           headers={"Authorization": f"Bearer {TOKEN}",
                    "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        data = json.loads(r.read())
    if not data.get("success"):
        raise RuntimeError(data.get("errors"))
    return data["result"][0]["results"]


def main() -> int:
    failures = []
    for table, sql, min_rows in CHECKS:
        try:
            rows = query(sql)
            count = rows[0]["n"] if rows else 0
            status = "OK" if count >= min_rows else "FAIL"
            print(f"[{status}] {table}: {count} rows (min {min_rows})")
            if status == "FAIL":
                failures.append(table)
        except Exception as exc:
            print(f"[ERROR] {table}: {exc}")
            failures.append(table)
    if failures:
        print(f"\nFailed: {', '.join(failures)}", file=sys.stderr)
        return 1
    print("\nAll checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
