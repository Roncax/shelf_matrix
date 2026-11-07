from dataclasses import replace

import pytest

from shelf_matrix.schema import (
    GLOBAL_PARAMS,
    Manifest,
    ManifestError,
    ParamSpec,
    load_manifest,
    resolve_params,
    validate_value,
)

CLOCK = """
id = "clock"
name = "Clock + weather"
kind = "python"
entry = "program:ClockProgram"
needs = ["weather"]

[[params]]
name = "style"
label = "Style"
type = "choice"
options = ["digital", "analog"]
default = "digital"
"""


def write(tmp_path, text):
    (tmp_path / "manifest.toml").write_text(text)


def test_load_manifest_reads_params(tmp_path):
    write(tmp_path, CLOCK)
    m = load_manifest(tmp_path)
    assert m.id == "clock" and m.kind == "python" and m.entry == "program:ClockProgram"
    assert m.params[0] == ParamSpec("style", "Style", "choice", "digital", options=("digital", "analog"))
    assert m.needs == ("weather",)
    assert m.path == tmp_path


def test_load_manifest_rejects_bad_default(tmp_path):
    write(tmp_path, """
id = "x"
name = "X"
kind = "python"
entry = "program:X"
[[params]]
name = "n"
label = "N"
type = "int"
default = 500
max = 100
""")
    with pytest.raises(ManifestError):
        load_manifest(tmp_path)


def test_load_manifest_rejects_python_without_entry(tmp_path):
    write(tmp_path, 'id = "x"\nname = "X"\nkind = "python"\n')
    with pytest.raises(ManifestError):
        load_manifest(tmp_path)


def test_load_manifest_rejects_command_without_command(tmp_path):
    write(tmp_path, 'id = "x"\nname = "X"\nkind = "command"\n')
    with pytest.raises(ManifestError):
        load_manifest(tmp_path)


def test_load_manifest_rejects_unknown_type_and_missing_keys(tmp_path):
    write(tmp_path, 'id = "x"\nname = "X"\nkind = "python"\nentry = "p:C"\n'
                    '[[params]]\nname = "n"\nlabel = "N"\ntype = "weird"\ndefault = 1\n')
    with pytest.raises(ManifestError):
        load_manifest(tmp_path)
    write(tmp_path, 'name = "X"\nkind = "python"\nentry = "p:C"\n')
    with pytest.raises(ManifestError):
        load_manifest(tmp_path)
    with pytest.raises(ManifestError):
        load_manifest(tmp_path / "nope")


@pytest.mark.parametrize("spec,value,expected", [
    (ParamSpec("c", "C", "color", "#000000"), "#FFAA00", "#ffaa00"),
    (ParamSpec("b", "B", "bool", True), False, False),
    (ParamSpec("f", "F", "float", 1.0, min=0, max=2), 1, 1.0),
    (ParamSpec("ch", "Ch", "choice", "a", options=("a", "b")), "b", "b"),
])
def test_validate_value_accepts(spec, value, expected):
    assert validate_value(spec, value) == expected


@pytest.mark.parametrize("spec,value", [
    (ParamSpec("c", "C", "color", "#000000"), "red"),
    (ParamSpec("i", "I", "int", 5, min=1, max=10), 11),
    (ParamSpec("i", "I", "int", 5, min=1, max=10), True),
    (ParamSpec("ch", "Ch", "choice", "a", options=("a", "b")), "z"),
    (ParamSpec("t", "T", "text", "", max_len=3), "abcd"),
])
def test_validate_value_rejects(spec, value):
    with pytest.raises(ValueError):
        validate_value(spec, value)


def test_resolve_params_replaces_invalid_and_drops_unknown():
    specs = [ParamSpec("i", "I", "int", 5, min=1, max=10), ParamSpec("c", "C", "color", "#ffffff")]
    assert resolve_params(specs, {"i": 99, "gone": 1}) == {"i": 5, "c": "#ffffff"}
    assert resolve_params(specs, None) == {"i": 5, "c": "#ffffff"}


def test_manifest_dict_roundtrip(tmp_path):
    write(tmp_path, CLOCK)
    m = load_manifest(tmp_path)
    assert Manifest.from_dict(m.to_dict()) == replace(m, path=None)


def test_global_params_brightness():
    assert GLOBAL_PARAMS[0].name == "brightness" and GLOBAL_PARAMS[0].default == 70
