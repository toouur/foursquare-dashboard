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


def _unmap(config_dir: Path, city: str) -> None:
    """Drop `city` from the copied city_merge.yaml.

    The detector exists to catch a district BEFORE anyone writes the mapping —
    and once it has fired for real, the mapping gets written (Buiucani was
    added to city_merge.yaml the day this check first flagged it). Copying the
    live config would then merge the fixture away before the detector ever
    sees it, so the test quietly stopped testing anything. Removing the entry
    restores the state the check is meant to police, whatever config says now.
    """
    path = config_dir / "city_merge.yaml"
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    kept = [ln for ln in lines if not ln.lstrip().startswith(f'"{city}":')]
    path.write_text("".join(kept), encoding="utf-8")


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


DISTRICT_CITY = "Buiucani"          # a sector of Chișinău, not a city
HOST_CITY = "Chișinău"


def test_new_name_inside_an_established_city_is_flagged_as_district(tmp_path):
    """'Buiucani' reads as an ordinary city name — only geography gives it away.

    No fold-collision (it shares no spelling with Chișinău), no shape pattern
    (no 'район'/'Province' suffix), pure ASCII — every textual rule passes it.
    The check-in lands 2.8 km from a city with thousands of its own, which is
    what the proximity verdict is for.
    """
    config_dir = tmp_path / "config"
    shutil.copytree(ROOT / "config", config_dir)
    _unmap(config_dir, DISTRICT_CITY)

    csv_path, ts0 = tmp_path / "checkins.csv", 1500000000
    with csv_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=COLUMNS)
        writer.writeheader()
        # one check-in in the district …
        writer.writerow({"date": str(ts0), "venue": "Brun", "venue_id": "v0",
                         "city": DISTRICT_CITY, "country": "Moldova",
                         "lat": "47.020862", "lng": "28.820293",
                         "category": "Coffee Shop", "checkin_id": "c0",
                         "source_app": "Swarm"})
        # … against a host city that dwarfs it
        for i in range(40):
            writer.writerow({"date": str(ts0 + 100 + i), "venue": f"V{i}",
                             "venue_id": f"v{i + 1}", "city": HOST_CITY,
                             "country": "Moldova", "lat": "47.024500",
                             "lng": "28.832300", "category": "Park",
                             "checkin_id": f"c{i + 1}", "source_app": "Swarm"})

    baseline = tmp_path / "baseline.json"
    _run(csv_path, config_dir, baseline, "--update-baseline")
    _drop_from_baseline(baseline, [DISTRICT_CITY])

    result = _run(csv_path, config_dir, baseline)

    assert "DISTRICT?" in result.stdout, result.stdout
    assert HOST_CITY in result.stdout
    # Reported, not blocking: real settlements do sit beside bigger neighbours.
    assert result.returncode == 0, result.stdout
