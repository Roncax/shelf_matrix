import json

from shelf_matrix.server.store import Store


def test_store_roundtrip(tmp_path):
    path = tmp_path / "data" / "store.json"
    s = Store(path)
    s.active_program = "clock"
    s.params = {"clock": {"color": "#112233"}}
    s.globals = {"brightness": 40}
    s.catalog = [{"id": "clock", "name": "Clock", "kind": "python", "params": []}]
    s.save()

    again = Store(path)
    assert again.active_program == "clock"
    assert again.params == {"clock": {"color": "#112233"}}
    assert again.globals == {"brightness": 40}
    assert again.catalog == [{"id": "clock", "name": "Clock", "kind": "python", "params": []}]
    assert not path.with_suffix(".tmp").exists()


def test_store_missing_file_starts_empty(tmp_path):
    s = Store(tmp_path / "nope.json")
    assert s.active_program is None
    assert s.params == {} and s.globals == {} and s.catalog == []


def test_store_corrupt_file_starts_empty(tmp_path):
    path = tmp_path / "store.json"
    path.write_text("{not json")
    s = Store(path)
    assert s.active_program is None
    assert s.params == {} and s.globals == {} and s.catalog == []


def test_store_wrong_types_start_empty(tmp_path):
    path = tmp_path / "store.json"
    path.write_text(json.dumps({"active_program": 3, "params": [], "globals": {}, "catalog": []}))
    s = Store(path)
    assert s.active_program is None
    assert s.params == {}


def test_store_failed_save_removes_tmp(tmp_path):
    path = tmp_path / "store.json"
    s = Store(path)
    s.globals = {"bad": object()}  # not JSON serialisable
    try:
        s.save()
    except TypeError:
        pass
    else:
        raise AssertionError("expected TypeError")
    assert not path.with_suffix(".tmp").exists()
    assert not path.exists()
