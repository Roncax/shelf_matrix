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
