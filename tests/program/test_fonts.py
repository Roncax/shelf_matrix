import os
from pathlib import Path

import pytest

from shelf_matrix.program.fonts import FontCache

FONTS = Path(__file__).resolve().parents[2] / "fonts"


def test_font_cache_loads_bdf(tmp_path):
    fc = FontCache(FONTS, tmp_path)
    assert "6x10" in fc.names()
    assert fc.names() == sorted(fc.names())
    assert fc.get("6x10").getbbox("12")[2] > 0
    assert (tmp_path / "6x10.pil").exists()
    assert fc.get("6x10") is fc.get("6x10")


def test_unknown_font(tmp_path):
    with pytest.raises(KeyError):
        FontCache(FONTS, tmp_path).get("nope")


def test_truncated_pil_is_reconverted(tmp_path):
    fc = FontCache(FONTS, tmp_path)
    fc.get("6x10")
    pil = tmp_path / "6x10.pil"
    pil.write_bytes(pil.read_bytes()[:20])
    fresh = FontCache(FONTS, tmp_path)
    assert fresh.get("6x10").getbbox("12")[2] > 0
    assert pil.stat().st_size > 20


def test_missing_pbm_triggers_conversion(tmp_path):
    FontCache(FONTS, tmp_path).get("6x10")
    (tmp_path / "6x10.pbm").unlink()
    assert FontCache(FONTS, tmp_path).get("6x10").getbbox("12")[2] > 0
    assert (tmp_path / "6x10.pbm").exists()


def test_conversion_leaves_no_temp_files(tmp_path):
    FontCache(FONTS, tmp_path).get("6x10")
    assert sorted(p.name for p in tmp_path.iterdir()) == ["6x10.pbm", "6x10.pil"]


def test_warm_converts_every_font(tmp_path):
    fc = FontCache(FONTS, tmp_path / "cache")
    fc.warm()
    for name in fc.names():
        assert (tmp_path / "cache" / f"{name}.pil").exists()
        assert (tmp_path / "cache" / f"{name}.pbm").exists()


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores directory permissions")
def test_unwritable_cache_falls_back_to_tempdir(tmp_path):
    cache = tmp_path / "cache"
    cache.mkdir()
    cache.chmod(0o555)
    try:
        assert FontCache(FONTS, cache).get("6x10").getbbox("12")[2] > 0
    finally:
        cache.chmod(0o755)
