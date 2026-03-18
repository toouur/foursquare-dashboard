"""gen_tips.py — Generate tips.html from data/tips.json."""
import json
from datetime import datetime, timezone
from pathlib import Path


def build_page(csv_path, config_dir, out_path, tmpl_path, tips_path=None):
    TEMPLATE = Path(tmpl_path).read_text(encoding="utf-8")

    tips_file = Path(tips_path) if tips_path else Path(csv_path).parent / "tips.json"

    if not tips_file.exists():
        html = TEMPLATE.replace("TIPS_DATA_PLACEHOLDER", "[]")
        Path(out_path).write_text(html, encoding="utf-8")
        print(f"tips.html -> {out_path}  (no tips data — run fetch_tips.py first)")
        return

    tips = json.loads(tips_file.read_text(encoding="utf-8"))
    tips.sort(key=lambda t: -t.get("ts", 0))

    for t in tips:
        ts = t.get("ts", 0)
        if ts:
            dt = datetime.fromtimestamp(ts, tz=timezone.utc)
            t["date"] = dt.strftime("%d %b %Y")
            t["time"] = dt.strftime("%H:%M")
        else:
            t["date"] = ""
            t["time"] = ""

    tips_json = json.dumps(tips, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    html = TEMPLATE.replace("TIPS_DATA_PLACEHOLDER", tips_json)
    Path(out_path).write_text(html, encoding="utf-8")
    size = Path(out_path).stat().st_size // 1024
    print(f"tips.html -> {out_path}  ({size}KB, {len(tips):,} tips)")
