from pathlib import Path

from shelf_matrix.client.catalog import load_catalog

GOOD = '''id = "{id}"
name = "N"
kind = "python"
entry = "program.py"
'''


def test_catalog_loads_real_programs():
    assert "clock" in load_catalog(Path("programs"))


def test_catalog_skips_invalid_manifest(tmp_path):
    (tmp_path / "good").mkdir()
    (tmp_path / "good" / "manifest.toml").write_text(GOOD.format(id="good"))
    (tmp_path / "bad").mkdir()
    (tmp_path / "bad" / "manifest.toml").write_text("not = [valid")
    (tmp_path / "nomanifest").mkdir()
    assert list(load_catalog(tmp_path)) == ["good"]


def test_catalog_skips_manifest_raising_type_error(tmp_path):
    (tmp_path / "bad").mkdir()
    (tmp_path / "bad" / "manifest.toml").write_text(
        'id = "bad"\nname = "B"\nkind = ["python"]\n')
    (tmp_path / "good").mkdir()
    (tmp_path / "good" / "manifest.toml").write_text(GOOD.format(id="good"))
    assert list(load_catalog(tmp_path)) == ["good"]


def test_catalog_duplicate_id_keeps_first_sorted(tmp_path):
    for d in ("b", "a"):
        (tmp_path / d).mkdir()
        (tmp_path / d / "manifest.toml").write_text(GOOD.format(id="dup"))
    assert load_catalog(tmp_path)["dup"].path.name == "a"
