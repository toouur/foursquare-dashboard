# Copyright 2026 Andrei Patsiomkin
# SPDX-License-Identifier: Apache-2.0

"""Every CARTO basemap tile URL carries the API key.

Since Sep 2026 basemaps.cartocdn.com answers keyless requests with an
"API KEY REQUIRED" watermark tile, which blanked every map on the site.
Each tile URL therefore ends in ?key={{CARTO_KEY}}, which build.py fills in
every generated page from settings.yaml basemap.carto_key (env CARTO_API_KEY
overrides). A tile layer without it — or one without the CARTO attribution
the free key's terms require — would quietly bring the watermark back.
"""
import re
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
# gen_flights.py renders through str.format(), so its braces are doubled.
KEY_PARAM = re.compile(r"\?key=(\{\{CARTO_KEY\}\}|\{\{\{\{CARTO_KEY\}\}\}\})'")


def _sources():
    yield from (REPO / "templates").glob("*.tmpl")
    yield from (REPO / "scripts").glob("*.py")


def _tile_layers():
    """(file, text of the L.tileLayer( call up to its closing `.addTo(`)."""
    for p in _sources():
        txt = p.read_text(encoding="utf-8")
        for m in re.finditer(r"L\.tileLayer\(\s*'http", txt):
            end = txt.find(".addTo(", m.start())
            yield p.name, txt[m.start():end if end != -1 else m.start() + 400]


def test_tile_layers_exist():
    assert sum(1 for _ in _tile_layers()) >= 10


def test_every_carto_url_carries_key():
    bad = [f"{n}: {c[:110]}" for n, c in _tile_layers()
           if "cartocdn.com" in c and not KEY_PARAM.search(c)]
    assert not bad, "CARTO tile URL without ?key={{CARTO_KEY}}:\n  " + "\n  ".join(bad)


def test_every_carto_layer_is_attributed():
    bad = [f"{n}: {c[:110]}" for n, c in _tile_layers()
           if "cartocdn.com" in c and "dark_only_labels" not in c and "CARTO" not in c.split("',", 1)[-1]]
    assert not bad, "CARTO tile layer without attribution:\n  " + "\n  ".join(bad)


def test_build_substitutes_carto_key():
    src = (REPO / "scripts" / "build.py").read_text(encoding="utf-8")
    assert 'replace("{{CARTO_KEY}}"' in src
    assert "CARTO_API_KEY" in src
