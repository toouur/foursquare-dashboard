"""check_city_count: a displayed 'city' that is not a settlement must block.

The invariant checks only covered encoding duplicates, so a name like
'Antwerp Province' or 'РФ / РБ' reached the displayed city set unnoticed: a
`city_fixes.json` value bypasses city_merge entirely (transform.py sets the
city and returns early), and an ASCII name tripped no other rule.
"""
from __future__ import annotations

import csv
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "check_city_count.py"
COLUMNS = ["date", "venue", "venue_id", "city", "country",
           "lat", "lng", "category", "checkin_id", "source_app"]

NOT_CITIES = ["РФ / РБ", "Antwerp Province", "Sejny - Lazdijai",
              "stancyja Hudahaj", "メトコビッチ"]


def _run(csv_path: Path, config_dir: Path, baseline: Path, *extra: str):
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--csv", str(csv_path),
         "--config-dir", str(config_dir), "--baseline", str(baseline), *extra],
        capture_output=True, text=True)


def _fixture(tmp_path: Path, cities: list[str]) -> tuple[Path, Path, Path]:
    """A CSV whose cities arrive through city_fixes, i.e. bypassing city_merge."""
    config_dir = tmp_path / "config"
    shutil.copytree(ROOT / "config", config_dir)

    csv_path, ts0 = tmp_path / "checkins.csv", 1500000000
    with csv_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=COLUMNS)
        writer.writeheader()
        for i, _ in enumerate(cities):
            writer.writerow({"date": str(ts0 + i), "venue": f"V{i}",
                             "venue_id": f"v{i}", "city": "", "country": "X",
                             "lat": "50", "lng": "30", "category": "Country",
                             "checkin_id": f"c{i}", "source_app": "Swarm"})

    fixes_path = config_dir / "city_fixes.json"
    fixes = json.loads(fixes_path.read_text(encoding="utf-8"))
    for i, city in enumerate(cities):
        fixes[str(ts0 + i)] = city
    fixes_path.write_text(json.dumps(fixes, ensure_ascii=False), encoding="utf-8")

    baseline = tmp_path / "baseline.json"
    _run(csv_path, config_dir, baseline, "--update-baseline")
    return csv_path, config_dir, baseline


def _drop_from_baseline(baseline: Path, cities: list[str]) -> None:
    """Make the cities look newly added rather than already accepted."""
    data = json.loads(baseline.read_text(encoding="utf-8"))
    for city in cities:
        data["counts"].pop(city, None)
    data["total_cities"] = len(data["counts"])
    baseline.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def test_new_non_settlement_name_blocks(tmp_path):
    csv_path, config_dir, baseline = _fixture(tmp_path, NOT_CITIES)
    _drop_from_baseline(baseline, NOT_CITIES)

    result = _run(csv_path, config_dir, baseline)

    assert result.returncode == 1, result.stdout
    assert "NOT A CITY" in result.stdout
    for city in NOT_CITIES:
        assert repr(city) in result.stdout


def test_non_settlement_already_in_baseline_reports_without_blocking(tmp_path):
    """Existing debt is surfaced, but it must not turn CI red on its own."""
    csv_path, config_dir, baseline = _fixture(tmp_path, NOT_CITIES)

    result = _run(csv_path, config_dir, baseline)

    assert result.returncode == 0, result.stdout
    assert "Not-a-city debt" in result.stdout


@pytest.mark.parametrize("city", ["Smolensk Region", "Biel/Bienne"])
def test_deliberate_canonical_label_is_not_flagged(city, tmp_path):
    """A region label someone chose on purpose is vouched by city_merge."""
    csv_path, config_dir, baseline = _fixture(tmp_path, [city])
    _drop_from_baseline(baseline, [city])

    result = _run(csv_path, config_dir, baseline)

    assert "NOT A CITY" not in result.stdout, result.stdout
