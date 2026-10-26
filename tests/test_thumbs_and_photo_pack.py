# Copyright 2026 Andrei Patsiomkin
# SPDX-License-Identifier: Apache-2.0

"""make_thumbs (pix/t/ copies) + gen_photos.pack_photos (the compact photos.html payload)."""
from __future__ import annotations

import io
import json
import shutil
import subprocess
from pathlib import Path

import pytest

import gen_photos
from make_thumbs import THUMB_PX, listed_keys, make_thumb, missing_keys, thumb_url

PIX = "https://x.r2.dev/pix"


def test_thumb_url():
    assert thumb_url(PIX + "/a.jpg") == PIX + "/t/a.jpg"
    assert thumb_url(PIX + "/t/a.jpg") == PIX + "/t/a.jpg"      # never twice
    assert thumb_url("a.jpg") == "a.jpg" and thumb_url("") == ""


def test_make_thumb_shrinks_and_rotates():
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (2000, 1000), (200, 40, 40)).save(buf, "PNG")
    with Image.open(io.BytesIO(make_thumb(buf.getvalue()))) as t:
        assert t.format == "JPEG" and max(t.size) == THUMB_PX and t.size[0] > t.size[1]


def test_listing_and_missing(tmp_path):
    o = tmp_path / "o.txt"
    o.write_text("2026-01-01 10:00:00   9000 a.jpg\n                           PRE t/\n"
                 "2026-01-01 10:00:00   9000 b.jpg\n2026-01-01 10:00:00 9 notes.txt\n", encoding="utf-8")
    t = tmp_path / "t.txt"
    t.write_text("2026-01-02 10:00:00   900 a.jpg\n", encoding="utf-8")
    assert listed_keys(str(o)) == {"a.jpg", "b.jpg"}
    assert missing_keys(listed_keys(str(o)), listed_keys(str(t))) == ["b.jpg"]


PHOTOS = [
    {"src": PIX + "/29447180_aaa.jpg", "venue": "Cafe", "city": "Minsk", "country": "Belarus", "ts": 1700000000},
    {"src": PIX + "/rf_bbb.jpg", "venue": "Cafe", "city": "Minsk", "country": "Belarus", "ts": 1600000000},
    {"src": PIX + "/29447180_ccc.jpg", "venue": "Bar", "city": "", "country": "", "ts": 0},
]


def test_pack_photos_tables():
    P = gen_photos.pack_photos(PHOTOS, PIX)
    assert P["V"] == ["Cafe", "Bar"] and P["C"] == ["Minsk", ""]
    assert [r[0] for r in P["r"]] == ["aaa.jpg", "!rf_bbb.jpg", "ccc.jpg"]


def _node():
    exe = shutil.which("node")
    if exe:
        return exe
    fb = Path("C:/Program Files/nodejs/node.exe")
    return str(fb) if fb.exists() else None


def test_unpack_in_js_gives_back_the_objects(tmp_path):
    node = _node()
    if not node:
        pytest.skip("node not installed — cannot run the JS side")
    src = Path(gen_photos.__file__).read_text(encoding="utf-8")
    js = src[src.index("const _MON"):src.index("const PHOTOS_NEWEST")].replace("{{", "{").replace("}}", "}")
    h = tmp_path / "h.js"
    h.write_text(js + f"\nprocess.stdout.write(JSON.stringify(unpackPhotos({json.dumps(gen_photos.pack_photos(PHOTOS, PIX))})));",
                 encoding="utf-8")
    out = json.loads(subprocess.run([node, str(h)], capture_output=True, timeout=30, check=True).stdout)
    assert [o["src"] for o in out] == [p["src"] for p in PHOTOS]
    assert out[0]["venue"] == "Cafe" and out[0]["date"] == "14 Nov 2023" and out[2]["date"] == ""
