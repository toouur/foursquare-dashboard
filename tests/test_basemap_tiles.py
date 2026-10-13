# Copyright 2026 Andrei Patsiomkin
# SPDX-License-Identifier: Apache-2.0

"""Every map uses a keyless basemap.

CARTO's basemaps.cartocdn.com started answering every tile with an
"API KEY REQUIRED" watermark (Oct 2026), which blanked all maps on the site.
The maps now use Esri's keyless Dark Gray Canvas; this guards against a
template or generator quietly bringing a key-gated provider back.
"""
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
KEY_GATED = ("basemaps.cartocdn.com", "tiles.stadiamaps.com")


def _sources():
    yield from (REPO / "templates").glob("*.tmpl")
    yield from (REPO / "scripts").glob("*.py")


def test_no_key_gated_basemap():
    hits = [f"{p.name}: {host}" for p in _sources()
            for host in KEY_GATED if host in p.read_text(encoding="utf-8")]
    assert not hits, "key-gated tile provider referenced:\n  " + "\n  ".join(hits)


def test_every_tile_layer_is_esri():
    for p in _sources():
        for line in p.read_text(encoding="utf-8").splitlines():
            if "L.tileLayer(" in line and "http" in line:
                assert "server.arcgisonline.com" in line, f"{p.name}: {line.strip()[:120]}"
