from shelf_matrix.client.state import LocalState, load_state, save_state


def test_save_then_load_roundtrip(tmp_path):
    p = tmp_path / "sub" / "state.json"
    s = LocalState(active_program="weather", params={"clock": {"a": 1}},
                   globals={"brightness": 50}, data={"x": {"y": 2}})
    save_state(p, s)
    assert load_state(p) == s


def test_load_missing_state_returns_default(tmp_path):
    assert load_state(tmp_path / "nope.json") == LocalState()


def test_load_corrupt_state_returns_default(tmp_path):
    p = tmp_path / "state.json"
    p.write_text('{"active_prog')
    assert load_state(p) == LocalState()


def test_save_is_atomic_no_tmp_left(tmp_path):
    p = tmp_path / "state.json"
    save_state(p, LocalState())
    assert sorted(x.name for x in tmp_path.iterdir()) == ["state.json"]
