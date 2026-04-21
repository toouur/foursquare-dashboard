"""Generate a DELETE+INSERT SQL dump for checkins and venues tables.

Used for full resyncs via wrangler d1 execute --remote, which is more
reliable than the Python batch-API path for large row counts.

Usage:
    python scripts/gen_d1_dump.py \\
        --csv  path/to/checkins.csv \\
        --out  /tmp/checkins_venues_dump.sql

Then execute:
    npx wrangler d1 execute swarmdata --file=<out> --remote
"""
import argparse, csv, os, sys
from collections import defaultdict


def q(v):
    if v is None:
        return "NULL"
    if isinstance(v, (int, float)):
        return str(v)
    return "'" + str(v).replace("'", "''") + "'"


def _str(v):
    if v is None:
        return None
    s = str(v).strip()
    return s if s else None


def _int(v):
    try:
        return int(v)
    except Exception:
        return None


def _float(v):
    try:
        f = float(v)
        return f if f == f else None  # NaN guard
    except Exception:
        return None


CHECKIN_COLS = (
    "(id,date,venue_id,venue,venue_url,city,state,country,neighborhood,"
    "lat,lng,address,category,shout,source_app,source_url,"
    "with_name,with_id,created_by_name,created_by_id,overlaps_name,overlaps_id)"
)
VENUE_COLS = (
    "(id,name,category,lat,lng,city,country,"
    "checkin_count,first_checkin_at,last_checkin_at)"
)


def generate(csv_path: str, out_path: str, batch: int = 100) -> None:
    checkin_rows = []
    venue_meta: dict = defaultdict(lambda: {
        "name": "", "category": "", "lat": None, "lng": None,
        "city": "", "country": "", "first_ts": 0, "last_ts": 0, "count": 0,
    })

    with open(csv_path, encoding="utf-8-sig", newline="") as fh:
        for row in csv.DictReader(fh):
            cid = _str(row.get("checkin_id"))
            if not cid:
                continue
            ts  = _int(row.get("date"))
            vid = _str(row.get("venue_id"))
            lat = _float(row.get("lat"))
            lng = _float(row.get("lng"))
            checkin_rows.append([
                cid, ts, vid,
                _str(row.get("venue")),
                _str(row.get("venue_url")),
                _str(row.get("city")),
                _str(row.get("state")),
                _str(row.get("country")),
                _str(row.get("neighborhood")),
                lat, lng,
                _str(row.get("address")),
                _str(row.get("category")),
                _str(row.get("shout")),
                _str(row.get("source_app")),
                _str(row.get("source_url")),
                _str(row.get("with_name")),
                _str(row.get("with_id")),
                _str(row.get("created_by_name")),
                _str(row.get("created_by_id")),
                _str(row.get("overlaps_name")),
                _str(row.get("overlaps_id")),
            ])
            if vid:
                m = venue_meta[vid]
                m["count"] += 1
                if ts and (not m["first_ts"] or ts < m["first_ts"]):
                    m["first_ts"] = ts
                if ts and ts > m["last_ts"]:
                    m["last_ts"]   = ts
                    m["name"]      = _str(row.get("venue")) or ""
                    m["category"]  = _str(row.get("category")) or ""
                    m["city"]      = _str(row.get("city")) or ""
                    m["country"]   = _str(row.get("country")) or ""
                    m["lat"]       = lat
                    m["lng"]       = lng

    print(f"Parsed: {len(checkin_rows):,} checkins, {len(venue_meta):,} venues", flush=True)

    with open(out_path, "w", encoding="utf-8") as out:
        out.write("DELETE FROM checkins;\n")
        out.write("DELETE FROM venues;\n")

        for i in range(0, len(checkin_rows), batch):
            chunk = checkin_rows[i:i + batch]
            vals = ",\n".join("(" + ",".join(q(v) for v in r) + ")" for r in chunk)
            out.write(f"INSERT INTO checkins {CHECKIN_COLS} VALUES\n{vals};\n")

        venue_rows = [
            [vid, m["name"], m["category"], m["lat"], m["lng"],
             m["city"], m["country"], m["count"],
             m["first_ts"] or None, m["last_ts"] or None]
            for vid, m in venue_meta.items()
        ]
        for i in range(0, len(venue_rows), batch):
            chunk = venue_rows[i:i + batch]
            vals = ",\n".join("(" + ",".join(q(v) for v in r) + ")" for r in chunk)
            out.write(f"INSERT INTO venues {VENUE_COLS} VALUES\n{vals};\n")

    size_mb = os.path.getsize(out_path) / 1024 / 1024
    print(f"Written: {out_path} ({size_mb:.1f} MB)", flush=True)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--csv",   required=True, help="Path to checkins.csv")
    ap.add_argument("--out",   required=True, help="Output SQL file path")
    ap.add_argument("--batch", type=int, default=100, help="Rows per INSERT statement (default: 100)")
    args = ap.parse_args()

    if not os.path.exists(args.csv):
        sys.exit(f"CSV not found: {args.csv}")

    generate(args.csv, args.out, args.batch)


if __name__ == "__main__":
    main()
