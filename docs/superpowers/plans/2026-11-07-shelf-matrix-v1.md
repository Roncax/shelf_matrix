# Shelf Matrix v1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A Pi Zero W drives a 64×64 LED matrix that boots into a clock+weather program, and a Dockerized server on the home LAN lets a phone switch programs, set per-program parameters and global brightness.

**Architecture:** One Python package `shelf_matrix` with three parts: `server/` (FastAPI, runs in Docker, serves a vanilla-JS one-page UI, stores state in a JSON file, fetches Open-Meteo weather), `client/` (systemd service on the Pi; connects out over WebSocket; supervises one program child process at a time; caches state locally so it runs offline), and `program/` (runtime that Python programs use; draws into a PIL image and pushes it to the matrix). Programs live in `programs/<id>/` with a TOML manifest that declares their parameter schema; the client reports manifests to the server, the UI generates controls from them.

**Tech Stack:** Python ≥3.10 (Pi runs 3.11), FastAPI + uvicorn, `websockets`, `httpx`, Pillow, `tomli` (py<3.11), pytest + pytest-asyncio, hzeller `rgbmatrix` Python bindings (Pi only), Docker, systemd.

**Spec:** `docs/superpowers/specs/2026-11-07-shelf-matrix-design.md`

## Global Constraints

- Pi client never listens on a port; it only connects out to `server_url`.
- The matrix must keep running when the server/Wi-Fi is down; on boot the client starts the cached program before contacting the server, or `clock` if there is no cache.
- One process drives the matrix at a time: the old child must have exited before the next starts.
- Adding a program or a parameter must never require editing server or UI code.
- Program switches may leave the panel dark for a few seconds; live param/brightness changes on Python programs must reach the matrix within ~1 s.
- Default matrix options = the owner's command minus `--led-show-refresh`: rows 64, cols 64, `adafruit-hat-pwm`, pwm bits 9, pwm lsb 50 ns, limit refresh 120 Hz, pixel mapper `Rotate:90`, hardware pulse **disabled** (until verified on device, Task 14).
- `rgbmatrix` must only be imported inside `shelf_matrix/program/backend.py`; everything else runs on a PC with `SHELF_MATRIX_FAKE=1`.
- Pi↔server auth: `Authorization: Bearer <token>` header on `/ws/client`; wrong/missing token → close code 4401.
- No login on the UI in v1.
- License: GPL-2.0-or-later (library is GPL-2.0).
- Nothing in this repo is vendored from the library except BDF fonts (copied with their README).

## Review Focus

1. **Corrupt or missing state file** (client `state.json` or server `store.json`, e.g. after power loss mid-write) → start with defaults, never crash. Tests: Task 6 (`test_load_corrupt_state_returns_default`), Task 9 (`test_store_corrupt_file_starts_empty`).
2. **Stored params that no longer match the manifest** (param removed, renamed, value out of range or wrong type after a program update) → silently replaced by the default. Test: Task 1 (`test_resolve_params_replaces_invalid_and_drops_unknown`).
3. **Settings changed in the UI while the Pi is offline** → server persists them; Pi converges on reconnect; UI shows Pi offline. Test: Task 11 (`test_changes_while_offline_are_synced_on_reconnect`).
4. **Program that cannot start** (import error, missing C++ binary, crash loop) → backoff, then fallback to `clock`, status reported to UI. Test: Task 7 (`test_crash_loop_falls_back_to_clock`, `test_missing_binary_falls_back`).
5. **Weather API failing / unknown city** → keep last value, no crash, dashes if never fetched. Tests: Task 10 (`test_fetch_failure_keeps_last_value`, `test_unknown_city_yields_no_data`), Task 5 (`test_digital_face_renders_without_weather`).

---

## File Structure

```
pyproject.toml                       package + extras [server] [client] [dev]
LICENSE                              GPL-2.0
README.md                            short: what, dev quickstart, deploy pointers
fonts/                               BDF fonts copied from library + its README
shelf_matrix/
  __init__.py                        __version__ = "0.1.0"
  schema.py                          ParamSpec, Manifest, load_manifest, validate_value, resolve_params, GLOBAL_PARAMS
  protocol.py                        JSON message encode/decode, preview binary frames
  config.py                          ClientConfig, MatrixOptions, load_client_config
  program/
    backend.py                       create_matrix(): real rgbmatrix or FakeMatrix
    fonts.py                         FontCache: BDF → PIL font, cached
    base.py                          Program base class, ProgramContext
    runtime.py                       run_program loop, stdin reader, stdout writer
    run.py                           `python -m shelf_matrix.program.run` entry
  client/
    state.py                         LocalState load/save (atomic)
    catalog.py                       load_catalog(programs_dir)
    supervisor.py                    Supervisor: child lifecycle, crash backoff, fallback
    link.py                          ServerLink: reconnecting WS client
    app.py                           ClientApp: wires state, supervisor, link
    __main__.py                      `python -m shelf_matrix.client --config ...`
  server/
    store.py                         Store: JSON-file persistence
    hub.py                           Hub: Pi connection, browsers, commands, broadcast
    weather.py                       Open-Meteo fetch + WeatherService
    app.py                           create_app(): FastAPI routes, WS endpoints
    static/index.html, app.js, controls.js, style.css
programs/
  clock/manifest.toml, program.py, faces.py, icons.py
  demo-life/manifest.toml            C++ demo -D7
  demo-sandpile/manifest.toml        C++ demo -D6
  demo-cube/manifest.toml            C++ demo -D12
deploy/
  config.example.toml                Pi client config
  shelf-matrix.service               systemd unit
  install-pi.sh                      Pi installer
  Dockerfile                         server image (build context = repo root)
  docker-compose.yml
  .env.example                       SHELF_TOKEN=
tests/                               mirrors package; tests/fixtures/programs/* test programs
```

---

### Task 1: Project scaffold + parameter schema

**Files:**
- Create: `pyproject.toml`, `LICENSE`, `README.md`, `.gitignore`, `shelf_matrix/__init__.py`, `shelf_matrix/schema.py`
- Test: `tests/test_schema.py`

**Interfaces:**
- Produces:
  - `@dataclass(frozen=True) ParamSpec(name: str, label: str, type: str, default: Any, options: tuple[str, ...] = (), min: float | None = None, max: float | None = None, step: float | None = None, max_len: int = 200)`; `type` ∈ `{"color","choice","bool","int","float","text"}`.
  - `@dataclass(frozen=True) Manifest(id: str, name: str, kind: str, params: tuple[ParamSpec, ...], entry: str | None = None, command: tuple[str, ...] | None = None, needs: tuple[str, ...] = (), path: Path | None = None)`; `kind` ∈ `{"python","command"}`; methods `to_dict() -> dict` (omits `path`) and `Manifest.from_dict(d: dict) -> Manifest`.
  - `load_manifest(path: Path) -> Manifest` — reads `path/manifest.toml`; raises `ManifestError(ValueError)` on missing keys, unknown type, default that fails its own validation, `python` without `entry`, `command` without `command`.
  - `validate_value(spec: ParamSpec, value: Any) -> Any` — returns normalized value or raises `ValueError`. Rules: color `^#[0-9a-fA-F]{6}$` → lowercased; choice ∈ options; bool is `bool`; int is `int` and not `bool`, within [min,max]; float accepts int/float (not bool), within [min,max], returned as float; text is `str`, len ≤ max_len.
  - `resolve_params(specs: Iterable[ParamSpec], stored: Mapping[str, Any] | None) -> dict[str, Any]` — every spec present; invalid → default; unknown keys dropped.
  - `GLOBAL_PARAMS: tuple[ParamSpec, ...] = (ParamSpec("brightness", "Brightness", "int", 70, min=1, max=100, step=1),)`

Manifest TOML format (exact):

```toml
id = "clock"
name = "Clock + weather"
kind = "python"                 # or "command"
entry = "program:ClockProgram"  # python: <module file in program dir>:<class>
# command = ["{library_dir}/examples-api-use/demo", "-D7", "-m", "{step_ms}", "{matrix_flags}"]
needs = ["weather"]

[[params]]
name = "style"
label = "Style"
type = "choice"
options = ["digital", "analog"]
default = "digital"
```

- [ ] **Step 1: Write `pyproject.toml`** — name `shelf-matrix`, `requires-python = ">=3.10"`, license `GPL-2.0-or-later`, deps `tomli; python_version<'3.11'`; extras `server = ["fastapi>=0.110", "uvicorn[standard]>=0.29", "httpx>=0.27"]`, `client = ["websockets>=12", "pillow>=9"]`, `dev = ["shelf-matrix[server,client]", "pytest>=8", "pytest-asyncio>=0.23"]`; `[tool.pytest.ini_options] asyncio_mode = "auto"`; package data includes `shelf_matrix/server/static/*`. `.gitignore`: `__pycache__/`, `.venv/`, `*.egg-info/`, `.superpowers/`. Create venv: `python3 -m venv .venv && .venv/bin/pip install -e '.[dev]'`.

- [ ] **Step 2: Write failing tests** in `tests/test_schema.py`:

```python
def test_load_manifest_reads_params(tmp_path):  # write the TOML above to tmp_path/"manifest.toml"
    m = load_manifest(tmp_path)
    assert m.id == "clock" and m.kind == "python" and m.entry == "program:ClockProgram"
    assert m.params[0] == ParamSpec("style", "Style", "choice", "digital", options=("digital", "analog"))
    assert m.needs == ("weather",)

def test_load_manifest_rejects_bad_default(tmp_path):  # int param, default 500, max 100
    with pytest.raises(ManifestError): load_manifest(tmp_path)

def test_load_manifest_rejects_python_without_entry(tmp_path): ...  # ManifestError

@pytest.mark.parametrize("spec,value,expected", [
    (ParamSpec("c","C","color","#000000"), "#FFAA00", "#ffaa00"),
    (ParamSpec("b","B","bool",True), False, False),
    (ParamSpec("f","F","float",1.0,min=0,max=2), 1, 1.0),
    (ParamSpec("ch","Ch","choice","a",options=("a","b")), "b", "b"),
])
def test_validate_value_accepts(spec, value, expected): assert validate_value(spec, value) == expected

@pytest.mark.parametrize("spec,value", [
    (ParamSpec("c","C","color","#000000"), "red"),
    (ParamSpec("i","I","int",5,min=1,max=10), 11),
    (ParamSpec("i","I","int",5,min=1,max=10), True),
    (ParamSpec("ch","Ch","choice","a",options=("a","b")), "z"),
    (ParamSpec("t","T","text","",max_len=3), "abcd"),
])
def test_validate_value_rejects(spec, value):
    with pytest.raises(ValueError): validate_value(spec, value)

def test_resolve_params_replaces_invalid_and_drops_unknown():
    specs = [ParamSpec("i","I","int",5,min=1,max=10), ParamSpec("c","C","color","#ffffff")]
    assert resolve_params(specs, {"i": 99, "gone": 1}) == {"i": 5, "c": "#ffffff"}
    assert resolve_params(specs, None) == {"i": 5, "c": "#ffffff"}

def test_manifest_dict_roundtrip(tmp_path):
    m = load_manifest(tmp_path)
    assert Manifest.from_dict(m.to_dict()) == replace(m, path=None)
```

- [ ] **Step 3: Run** `.venv/bin/pytest tests/test_schema.py -v` → FAIL (import error).
- [ ] **Step 4: Implement `shelf_matrix/schema.py`** per Interfaces (TOML via `tomllib`, falling back to `tomli`).
- [ ] **Step 5: Run** `.venv/bin/pytest tests/test_schema.py -v` → PASS.
- [ ] **Step 6: Commit** `feat: project scaffold and parameter schema`.

---

### Task 2: Protocol

**Files:**
- Create: `shelf_matrix/protocol.py`
- Test: `tests/test_protocol.py`

**Interfaces:**
- Produces:
  - `PROTOCOL_VERSION = 1`; `PREVIEW_SIZE = 64`; `PREVIEW_BYTES = 64*64*3`.
  - `@dataclass Message(kind: str, payload: dict)`; `class ProtocolError(ValueError)`.
  - `encode(kind: str, **payload) -> str` → JSON `{"v": 1, "kind": kind, **payload}`.
  - `decode(text: str | bytes) -> Message` — raises `ProtocolError` on invalid JSON, non-object, missing `kind`, or `v != 1`.
  - `encode_preview(rgb: bytes) -> bytes` → `b"\x01" + rgb` (requires `len(rgb) == PREVIEW_BYTES`, else `ValueError`); `decode_preview(data: bytes) -> bytes` (raises `ProtocolError` on wrong prefix/length).
- Message kinds (payload keys) used by later tasks:
  - Pi→server: `hello {client_version, catalog: [Manifest.to_dict()], state: {active_program, params, globals}}`; `status {active_program, state: "starting"|"running"|"crashed"|"fallback", detail: str}`; binary preview.
  - Server→Pi: `sync {active_program, params: {program_id: {...}}, globals}`; `set_program {program, params}`; `set_params {program, params}` (full resolved dict); `set_globals {globals}`; `data {topic, key, value}`; `preview_on {}`; `preview_off {}`. Reserved, not implemented: `frame`.
  - Browser→server: `select_program {program}`; `update_params {program, params}` (partial); `update_globals {globals}` (partial).
  - Server→browser: `state {catalog, active_program, params, globals, client: {online, last_seen, status}}`; `error {message}`; binary preview.

- [ ] **Step 1: Write failing tests**: `test_encode_decode_roundtrip` (`decode(encode("set_globals", globals={"brightness": 40}))` → `Message("set_globals", {"globals": {"brightness": 40}})`); `test_decode_rejects_garbage` (parametrize `"not json"`, `"[]"`, `'{"v":1}'`, `'{"v":2,"kind":"x"}'` → `ProtocolError`); `test_preview_roundtrip` (`decode_preview(encode_preview(bytes(12288))) == bytes(12288)`); `test_preview_rejects_wrong_length`.
- [ ] **Step 2: Run** `.venv/bin/pytest tests/test_protocol.py -v` → FAIL.
- [ ] **Step 3: Implement** `shelf_matrix/protocol.py`.
- [ ] **Step 4: Run** → PASS.
- [ ] **Step 5: Commit** `feat: wire protocol`.

---

### Task 3: Client config + matrix options

**Files:**
- Create: `shelf_matrix/config.py`, `deploy/config.example.toml`
- Test: `tests/test_config.py`

**Interfaces:**
- Produces:
  - `@dataclass MatrixOptions(rows=64, cols=64, hardware_mapping="adafruit-hat-pwm", pwm_bits=9, pwm_lsb_nanoseconds=50, limit_refresh_rate_hz=120, pixel_mapper_config="Rotate:90", disable_hardware_pulsing=True, gpio_slowdown: int | None = None)`.
    - `to_cli_flags(brightness: int) -> list[str]` — order: `--led-rows=`, `--led-cols=`, `--led-gpio-mapping=`, `--led-pwm-bits=`, `--led-pwm-lsb-nanoseconds=`, `--led-limit-refresh=`, `--led-pixel-mapper=`, `--led-no-hardware-pulse` (only if disabled), `--led-slowdown-gpio=` (only if set), `--led-brightness=`.
    - `apply_to(opts) -> None` — sets same-named attributes on an `RGBMatrixOptions` (skip `gpio_slowdown` when None).
  - `@dataclass ClientConfig(server_url: str, token: str, library_dir: Path, programs_dir: Path, fonts_dir: Path, state_path: Path, fallback_program: str = "clock", matrix: MatrixOptions = field(default_factory=MatrixOptions), path: Path | None = None)`.
  - `load_client_config(path: Path) -> ClientConfig` — TOML, `[matrix]` table optional, missing required keys → `ValueError` naming the key.
- `deploy/config.example.toml` contains every key with the defaults above, `server_url = "ws://homeserver.lan:8080/ws/client"`, `token = "change-me"`, `library_dir = "/opt/rpi-rgb-led-matrix"`, `programs_dir = "/opt/shelf-matrix/programs"`, `fonts_dir = "/opt/shelf-matrix/fonts"`, `state_path = "/var/lib/shelf-matrix/state.json"`.

- [ ] **Step 1: Write failing tests**:

```python
def test_default_flags_match_owner_command():
    assert MatrixOptions().to_cli_flags(70) == [
        "--led-rows=64", "--led-cols=64", "--led-gpio-mapping=adafruit-hat-pwm",
        "--led-pwm-bits=9", "--led-pwm-lsb-nanoseconds=50", "--led-limit-refresh=120",
        "--led-pixel-mapper=Rotate:90", "--led-no-hardware-pulse", "--led-brightness=70"]

def test_example_config_loads():
    cfg = load_client_config(Path("deploy/config.example.toml"))
    assert cfg.matrix == MatrixOptions() and cfg.fallback_program == "clock"

def test_missing_server_url_is_error(tmp_path): ...  # ValueError mentioning "server_url"

def test_apply_to_sets_attributes():
    o = types.SimpleNamespace(); MatrixOptions(gpio_slowdown=2).apply_to(o)
    assert o.pixel_mapper_config == "Rotate:90" and o.gpio_slowdown == 2 and o.disable_hardware_pulsing is True
```

- [ ] **Step 2: Run** `.venv/bin/pytest tests/test_config.py -v` → FAIL.
- [ ] **Step 3: Implement** `config.py` and write `deploy/config.example.toml`.
- [ ] **Step 4: Run** → PASS.
- [ ] **Step 5: Commit** `feat: client config and matrix options`.

---

### Task 4: Program runtime (fake backend, fonts, IPC)

**Files:**
- Create: `shelf_matrix/program/__init__.py`, `backend.py`, `fonts.py`, `base.py`, `runtime.py`, `run.py`; `fonts/` (copy from `/home/roncax/projects/rpi-rgb-led-matrix/fonts/`: `4x6.bdf 5x7.bdf 5x8.bdf 6x10.bdf 6x13B.bdf 7x13.bdf 7x13B.bdf 8x13B.bdf 9x15B.bdf 9x18B.bdf 10x20.bdf tom-thumb.bdf README AUTHORS`)
- Test: `tests/program/test_fonts.py`, `tests/program/test_runtime.py`, `tests/fixtures/programs/solid/{manifest.toml,program.py}`

**Interfaces:**
- Consumes: `MatrixOptions.apply_to`, `ClientConfig`, `load_client_config` (Task 3), `load_manifest`, `resolve_params`, `GLOBAL_PARAMS` (Task 1).
- Produces:
  - `create_matrix(options: MatrixOptions, brightness: int)` — if env `SHELF_MATRIX_FAKE == "1"` return `FakeMatrix(64, 64)`, else import `rgbmatrix`, build `RGBMatrixOptions`, `apply_to`, set `brightness`, return `RGBMatrix(options=...)`. `FakeMatrix` implements the subset used: `brightness` attribute, `CreateFrameCanvas()`, `SwapOnVSync(canvas) -> canvas`; its canvas has `SetImage(img)`; `FakeMatrix.last_image: Image | None`; if env `SHELF_MATRIX_FAKE_PNG` is set, saves each swapped frame there (for eyeballing on a PC).
  - `FontCache(fonts_dir: Path, cache_dir: Path)`; `.names() -> list[str]` (BDF stems, sorted); `.get(name: str) -> ImageFont.ImageFont` — converts via `PIL.BdfFontFile` to `.pil` in `cache_dir` once; unknown name → `KeyError`.
  - `@dataclass ProgramContext(params: dict, globals: dict, data: dict[str, dict[str, Any]], fonts: FontCache, width: int = 64, height: int = 64)`.
  - `class Program`: class attr `fps: float = 1.0`; `__init__(self, ctx: ProgramContext)`; `on_update(self) -> None` (called after params/globals/data change; default no-op); `draw(self, img: Image.Image, now: datetime) -> None` (abstract; `img` is a black 64×64 RGB image each frame).
  - Child IPC, JSON lines. Parent→child on stdin: first line `{"kind":"init","params":{},"globals":{},"data":{},"preview":false}`, then any of `{"kind":"params","params":{}}`, `{"kind":"globals","globals":{}}`, `{"kind":"data","topic":..,"key":..,"value":..}`, `{"kind":"preview","on":bool}`. Child→parent on stdout: `{"kind":"ready"}` after first frame; `{"kind":"preview","rgb":"<base64 of 12288 bytes>"}` at most once per second while preview on. Logs go to stderr. Stdin EOF → child exits 0.
  - `run_program(program: Program, matrix, ctx: ProgramContext, inbox: queue.Queue[dict], out: TextIO, max_frames: int | None = None, now: Callable[[], datetime] = datetime.now) -> None` — loop: drain inbox (apply to ctx, brightness to `matrix.brightness`, call `on_update`; a `{"kind":"eof"}` item ends the loop), new black image, `draw`, `canvas.SetImage(img)`, `canvas = matrix.SwapOnVSync(canvas)`, emit ready/preview, sleep to hold `fps`.
  - Entry: `python -m shelf_matrix.program.run --config <client config path> <program_dir>` — loads manifest, imports `entry` from the program dir (add dir to `sys.path`), reads init line, resolves params, creates matrix, starts a daemon thread reading stdin into the inbox, calls `run_program`. Font cache dir: `state_path.parent / "fontcache"`.

- [ ] **Step 1: Write fixture program** `tests/fixtures/programs/solid`: manifest with `kind="python"`, `entry="program:Solid"`, one param `color` (color, default `#ff0000`); `Solid.draw` fills the image with `ctx.params["color"]`; `fps = 20`.
- [ ] **Step 2: Write failing tests**:

```python
def test_font_cache_loads_bdf(tmp_path):
    fc = FontCache(Path("fonts"), tmp_path)
    assert "6x10" in fc.names()
    assert fc.get("6x10").getbbox("12")[2] > 0

def test_run_program_applies_params_live():
    m = FakeMatrix(64, 64); ctx = make_ctx(params={"color": "#ff0000"})  # helper in test file
    inbox = queue.Queue(); out = io.StringIO()
    inbox.put({"kind": "params", "params": {"color": "#00ff00"}})
    run_program(Solid(ctx), m, ctx, inbox, out, max_frames=2)
    assert m.last_image.getpixel((10, 10)) == (0, 255, 0)

def test_run_program_sets_brightness():  # globals {"brightness": 30} → m.brightness == 30

def test_preview_emitted_only_when_on():
    # first run without preview → no "preview" line; then with {"kind":"preview","on":True}
    # → a line whose base64-decoded rgb has len 12288 and first pixel (255,0,0)

def test_child_process_end_to_end(tmp_path, client_config_file):  # fixture writes config with fake paths
    # spawn [sys.executable, "-m", "shelf_matrix.program.run", "--config", cfg, "tests/fixtures/programs/solid"]
    # env SHELF_MATRIX_FAKE=1; write init line with preview true; read stdout until "ready" and a preview line (timeout 10 s);
    # close stdin → process exits with code 0 within 5 s
```

- [ ] **Step 3: Run** `.venv/bin/pytest tests/program -v` → FAIL.
- [ ] **Step 4: Copy fonts** into `fonts/`, then implement `backend.py`, `fonts.py`, `base.py`, `runtime.py`, `run.py` per Interfaces.
- [ ] **Step 5: Run** `.venv/bin/pytest tests/program -v` → PASS.
- [ ] **Step 6: Commit** `feat: program runtime with fake backend and BDF fonts`.

---

### Task 5: Clock + weather program

**Files:**
- Create: `programs/clock/manifest.toml`, `programs/clock/program.py`, `programs/clock/faces.py`, `programs/clock/icons.py`
- Test: `tests/programs/test_clock.py` (adds `programs/clock` to `sys.path`)

**Interfaces:**
- Consumes: `Program`, `ProgramContext`, `FontCache` (Task 4).
- Produces:
  - Manifest: id `clock`, name `Clock + weather`, `entry = "program:ClockProgram"`, `needs = ["weather"]`, params:
    - `style`: choice `digital|analog`, default `digital`.
    - `color`: color, default `#ff9a3c`.
    - `accent`: color, default `#66ccff`.
    - `font`: choice `["6x10","7x13B","8x13B","9x15B","9x18B","10x20"]`, default `9x18B`.
    - `hour24`: bool, default `true`.
    - `seconds`: bool, default `false`.
    - `location`: text, default `""`, max_len 80.
  - `icons.weather_group(code: int, is_day: bool) -> str` — WMO code → one of `clear_day, clear_night, partly, cloudy, fog, drizzle, rain, snow, storm`. Codes: 0 clear; 1–2 partly; 3 cloudy; 45,48 fog; 51–57 drizzle; 61–67, 80–82 rain; 71–77, 85–86 snow; 95–99 storm; unknown → cloudy.
  - `icons.draw_icon(img, x: int, y: int, group: str, size: int = 9) -> None` — procedural pixel art.
  - `faces.render_digital(img, now: datetime, params: dict, weather: dict | None, fonts: FontCache) -> None`.
  - `faces.render_analog(img, now: datetime, params: dict, weather: dict | None, fonts: FontCache) -> None`.
  - Weather dict shape (produced by Task 10): `{"location": str, "temp_c": float, "code": int, "is_day": bool, "min_c": float, "max_c": float, "fetched_at": str}`.
  - `ClockProgram(Program)`: `fps = 1`, or `4` when `style == "analog"` and `seconds` is on, so the second hand moves without lag. `draw` picks the face; weather = `ctx.data.get("weather", {}).get(params["location"])`.
- Layout guidance (the builder designs the look): digital = time centered in the upper ~2/3 using `font`; a bottom strip with date (`ddd dd`) on the left and the icon plus `12°` on the right in `accent`; dashes `--°` when weather is None. Analog = a dial of 12 tick marks with radius ~24 centered at (32,28 or so), hands in `color`, second hand in `accent`, and the weather icon + temp in a corner. All text from `FontCache` (small text: `5x7` / `4x6` / `tom-thumb`).

- [ ] **Step 1: Write failing tests**:

```python
def test_weather_groups():
    assert weather_group(0, True) == "clear_day" and weather_group(0, False) == "clear_night"
    assert weather_group(63, True) == "rain" and weather_group(95, True) == "storm" and weather_group(1234, True) == "cloudy"

def test_digital_face_draws_time_in_color(fonts):
    img = Image.new("RGB", (64, 64)); render_digital(img, datetime(2026,11,7,21,47), DEFAULTS, WEATHER, fonts)
    assert (255, 154, 60) in set(img.crop((0, 0, 64, 40)).getdata())

def test_digital_face_renders_without_weather(fonts):
    img = Image.new("RGB", (64, 64)); render_digital(img, datetime(2026,11,7,9,5), DEFAULTS, None, fonts)
    assert img.getbbox() is not None  # drew something, no exception

def test_analog_hands_point_correctly(fonts):
    # 03:00 → a pixel ~10 px right of the dial centre is in `color`; 12:00 → ~10 px above centre is in `color`
    # (use faces.DIAL_CENTER exported constant)

def test_twelve_hour_format_changes_output(fonts):
    a, b = blank(), blank(); render_digital(a, datetime(2026,1,1,21,0), DEFAULTS, None, fonts)
    render_digital(b, datetime(2026,1,1,21,0), {**DEFAULTS, "hour24": False}, None, fonts)
    assert a.tobytes() != b.tobytes()

def test_every_font_option_fits(fonts):
    # for each font in manifest options: render "23:59" digital; no pixel of `color` at x<0 overflow → assert bbox within 64 width
```

- [ ] **Step 2: Run** `.venv/bin/pytest tests/programs -v` → FAIL.
- [ ] **Step 3: Implement** `icons.py`, `faces.py`, `program.py`, `manifest.toml`.
- [ ] **Step 4: Run** → PASS.
- [ ] **Step 5: Eyeball on a PC**: run the program as a child process with `SHELF_MATRIX_FAKE=1 SHELF_MATRIX_FAKE_PNG=/tmp/.../clock.png`, then scale the PNG ×8 and look at both styles (Read the image). Adjust until legible.
- [ ] **Step 6: Commit** `feat: clock + weather program (digital and analog)`.

---

### Task 6: Client local state + catalog

**Files:**
- Create: `shelf_matrix/client/__init__.py`, `state.py`, `catalog.py`
- Test: `tests/client/test_state.py`, `tests/client/test_catalog.py`

**Interfaces:**
- Consumes: `load_manifest`, `Manifest`, `ManifestError`, `resolve_params`, `GLOBAL_PARAMS` (Task 1).
- Produces:
  - `@dataclass LocalState(active_program: str = "clock", params: dict[str, dict] = {}, globals: dict = {}, data: dict[str, dict[str, Any]] = {})` (use `field(default_factory=dict)`).
  - `load_state(path: Path) -> LocalState` — missing, unreadable or invalid JSON → default `LocalState()` (log a warning).
  - `save_state(path: Path, state: LocalState) -> None` — write `path.with_suffix(".tmp")`, fsync, `os.replace`; create parent dirs.
  - `load_catalog(programs_dir: Path) -> dict[str, Manifest]` — every subdir with `manifest.toml`; invalid manifests are logged and skipped; duplicate id → keep first by sorted dir name, log.

- [ ] **Step 1: Write failing tests**: `test_save_then_load_roundtrip`; `test_load_missing_state_returns_default`; `test_load_corrupt_state_returns_default` (file content `{"active_prog`); `test_save_is_atomic_no_tmp_left`; `test_catalog_loads_real_programs` (`load_catalog(Path("programs"))` contains `clock`); `test_catalog_skips_invalid_manifest` (tmp dir with one good, one bad).
- [ ] **Step 2: Run** `.venv/bin/pytest tests/client -v` → FAIL.
- [ ] **Step 3: Implement** `state.py`, `catalog.py`.
- [ ] **Step 4: Run** → PASS.
- [ ] **Step 5: Commit** `feat: client state cache and program catalog`.

---

### Task 7: Supervisor (child lifecycle, C++ demos, crash fallback)

**Files:**
- Create: `shelf_matrix/client/supervisor.py`, `programs/demo-life/manifest.toml`, `programs/demo-sandpile/manifest.toml`, `programs/demo-cube/manifest.toml`
- Test: `tests/client/test_supervisor.py`, fixtures `tests/fixtures/programs/{crasher,sleeper}/manifest.toml` (+ `crasher/program.py` raising in `__init__`; `sleeper` is `kind="command"` with `command = ["{python}", "-c", "import time,sys; print(sys.argv[1:], flush=True); time.sleep(60)", "{matrix_flags}"]`)

**Interfaces:**
- Consumes: `ClientConfig`, `MatrixOptions.to_cli_flags` (Task 3), `Manifest`, `resolve_params` (Task 1), the child IPC + entry (Task 4).
- Produces:
  - `class Supervisor`:
    - `__init__(self, config: ClientConfig, catalog: dict[str, Manifest], on_status: Callable[[dict], Awaitable[None]], on_preview: Callable[[bytes], Awaitable[None]], *, backoff: tuple[float, ...] = (1, 2, 4, 8, 16, 30), crash_window_s: float = 60.0, max_crashes: int = 5, stop_timeout_s: float = 5.0)`.
    - `async start(self, program_id: str, params: dict, globals: dict, data: dict) -> None` — stops the current child first (waits for exit), spawns the new one, emits status `starting`, then `running` (on `ready` for python, immediately for command).
    - `async update_params(self, params: dict)`, `async update_globals(self, globals: dict)` — python: write IPC line; command: restart with new argv.
    - `async push_data(self, topic: str, key: str, value)` — python only, otherwise ignored.
    - `async set_preview(self, on: bool)` — python only.
    - `async stop(self)` — SIGTERM, wait `stop_timeout_s`, SIGKILL.
    - `current: str | None` property.
  - Command argv building: `build_command(manifest: Manifest, params: dict, config: ClientConfig, brightness: int) -> list[str]` — an element exactly `"{matrix_flags}"` is replaced by the `to_cli_flags(brightness)` list; other elements are `str.format` with `library_dir`, `python` (= `sys.executable`), and params.
  - Python child argv: `[sys.executable, "-m", "shelf_matrix.program.run", "--config", str(config.path), str(manifest.path)]`. Child stderr is inherited (goes to journald).
  - Unknown program id → start `config.fallback_program` and report `fallback`.
  - Crash handling: an unexpected exit (or failure to spawn, e.g. `FileNotFoundError`) → status `crashed` with detail, restart after the next backoff delay; `max_crashes` within `crash_window_s` → start the fallback program, status `fallback`. If the fallback itself crash-loops, keep retrying it with the max backoff.
- Demo manifests: `kind = "command"`, `command = ["{library_dir}/examples-api-use/demo", "-D<n>", "-m", "{step_ms}", "{matrix_flags}"]`; param `step_ms` int (min 10, max 2000, step 10). Life `-D7`, default 100. Sandpile `-D6`, default 50. Cube `-D12` has no `-m`, so its command omits `-m {step_ms}` and it has no params.

- [ ] **Step 1: Write failing tests** (tests use a `ClientConfig` whose `programs_dir` is `tests/fixtures/programs` merged with `programs/`, and `SHELF_MATRIX_FAKE=1` via `monkeypatch.setenv`; collect statuses into a list):

```python
def test_build_command_for_demo(config):
    m = load_manifest(Path("programs/demo-life"))
    argv = build_command(m, {"step_ms": 100}, config, 70)
    assert argv[:4] == [f"{config.library_dir}/examples-api-use/demo", "-D7", "-m", "100"]
    assert argv[4:] == config.matrix.to_cli_flags(70)

async def test_start_python_program_reports_running(sup, statuses): # start("solid") → statuses end with state "running" within 10 s
async def test_switch_stops_previous_child_first(sup):  # start solid, start sleeper → old process returncode is not None before new pid exists
async def test_command_restarts_on_globals_change(sup):  # start sleeper; update_globals({"brightness": 20}) → new pid, argv contains "--led-brightness=20"
async def test_crash_loop_falls_back_to_clock(sup_fast, statuses):  # backoff=(0.05,), max_crashes=3; start("crasher") → a "fallback" status and sup.current == "clock"
async def test_missing_binary_falls_back(sup_fast, config):  # demo-life with library_dir=tmp_path (no binary) → fallback to clock
async def test_unknown_program_falls_back(sup, statuses):  # start("nope") → current == "clock", status "fallback"
async def test_preview_forwarded(sup):  # start solid, set_preview(True) → on_preview receives 12288 bytes within 5 s
```

- [ ] **Step 2: Run** `.venv/bin/pytest tests/client/test_supervisor.py -v` → FAIL.
- [ ] **Step 3: Implement** `supervisor.py` with `asyncio.create_subprocess_exec` (stdin/stdout pipes for python children; a reader task parses stdout lines, base64-decodes previews). Write the three demo manifests.
- [ ] **Step 4: Run** → PASS.
- [ ] **Step 5: Commit** `feat: supervisor with crash fallback and C++ demo programs`.

---

### Task 8: Client server link + app

**Files:**
- Create: `shelf_matrix/client/link.py`, `shelf_matrix/client/app.py`, `shelf_matrix/client/__main__.py`
- Test: `tests/client/test_app.py`

**Interfaces:**
- Consumes: `Supervisor` (Task 7), `LocalState`, `load_state`, `save_state`, `load_catalog` (Task 6), protocol (Task 2), `ClientConfig` (Task 3).
- Produces:
  - `class ServerLink`: `__init__(self, url: str, token: str, on_connect: Callable[[], Awaitable[None]], on_message: Callable[[Message], Awaitable[None]], *, backoff=(1, 2, 5, 10, 30, 60))`; `async run(self)` (forever: connect with header `Authorization: Bearer <token>`, call `on_connect`, dispatch decoded messages, log and skip `ProtocolError`, reconnect with backoff on any error; backoff resets after a successful connect); `async send_json(self, kind: str, **payload) -> bool` and `async send_bytes(self, data: bytes) -> bool` (False when disconnected; never raises); `connected: bool`.
  - `class ClientApp`: `__init__(self, config: ClientConfig)`; `async run(self)`:
    1. load state + catalog;
    2. `supervisor.start(state.active_program, resolve_params(manifest, state.params.get(id)), resolve_params(GLOBAL_PARAMS, state.globals), state.data)` **before** the link starts;
    3. run the link.
    - `on_connect` sends `hello`.
    - Message handling, persisting state after each: `sync` (replace params/globals; start the program if it differs, otherwise push params and globals); `set_program`; `set_params` (only when `program == current`, otherwise just store); `set_globals`; `data` (store under `data[topic][key]`, push); `preview_on/off`.
    - Supervisor status → `status` message; preview → `send_bytes(encode_preview(...))`.
  - `python -m shelf_matrix.client --config /etc/shelf-matrix/config.toml` → `asyncio.run(ClientApp(cfg).run())`; logging to stderr at INFO.

- [ ] **Step 1: Write failing tests** (a tiny `websockets.serve` fake server on a free port in the test; `SHELF_MATRIX_FAKE=1`; config `state_path` in `tmp_path`):

```python
async def test_starts_cached_program_without_server(config_unreachable, tmp_path):
    save_state(config.state_path, LocalState(active_program="solid"))
    app task running 3 s → app.supervisor.current == "solid"  # server URL points to a closed port

async def test_sends_hello_with_catalog_and_token(fake_server):
    # fake server records the request's Authorization header and first message
    assert header == "Bearer t0ken" and first.kind == "hello" and "clock" in [m["id"] for m in first.payload["catalog"]]

async def test_sync_switches_program_and_persists(fake_server):
    # server sends sync{active_program:"solid", params:{"solid":{"color":"#00ff00"}}, globals:{"brightness":20}}
    # → supervisor.current == "solid"; load_state(...).params["solid"]["color"] == "#00ff00"

async def test_reconnects_after_server_restart(fake_server):  # close server, reopen on same port → a second hello arrives within 10 s (backoff=(0.1,))
```

- [ ] **Step 2: Run** `.venv/bin/pytest tests/client/test_app.py -v` → FAIL.
- [ ] **Step 3: Implement** `link.py`, `app.py`, `__main__.py`.
- [ ] **Step 4: Run** `.venv/bin/pytest tests/client -v` → PASS.
- [ ] **Step 5: Commit** `feat: client server link and app`.

---

### Task 9: Server store + hub

**Files:**
- Create: `shelf_matrix/server/__init__.py`, `store.py`, `hub.py`
- Test: `tests/server/test_store.py`, `tests/server/test_hub.py`

**Interfaces:**
- Consumes: `Manifest`, `validate_value`, `resolve_params`, `GLOBAL_PARAMS` (Task 1), protocol (Task 2).
- Produces:
  - `class Store(path: Path)`, holding the JSON doc `{"active_program": str | None, "params": {id: {...}}, "globals": {...}, "catalog": [manifest dicts]}`. Attributes are mirrored as properties; `save()` is atomic like `save_state`. A corrupt or missing file → empty defaults (`active_program=None`).
  - `class Hub(store: Store)`:
    - Peers are abstract: `Peer` protocol with `async send_text(str)` and `async send_bytes(bytes)`.
    - `async client_connected(peer) -> None`, `async client_disconnected(peer) -> None`. A newer Pi connection replaces the older one, and the older one gets closed by the caller.
    - `async on_client_message(msg: Message)`:
      - `hello`: store the catalog. If `store.active_program is None`, adopt the Pi's state; otherwise the server wins. Reply `sync` with resolved params for every catalog program, then `preview_on` if any browser is connected, then broadcast state.
      - `status`: keep it, broadcast.
    - `async on_client_preview(data: bytes)` → forwarded to all browsers.
    - `async browser_connected(peer)` → sends `state`; the first browser triggers `preview_on` to the Pi. `async browser_disconnected(peer)` → the last one leaving triggers `preview_off`.
    - `async on_browser_message(peer, msg)`:
      - `select_program`: unknown id → `error` to that peer.
      - `update_params`: validate each key with `validate_value`; an invalid value → `error` with the message, and nothing is applied.
      - `update_globals`: same validation as `update_params`.
      - On success: persist, send `set_program` / `set_params` / `set_globals` to the Pi if connected, broadcast `state`.
    - `state_payload() -> dict` → `{catalog, active_program, params (resolved), globals (resolved), client: {online, last_seen (ISO or None), status}}`.
    - Hook for weather: `on_params_changed: list[Callable[[], Awaitable[None]]]`, called after any params change or catalog update (Task 10 subscribes).
    - `async publish_data(topic: str, key: str, value)` → sends `data` to the Pi if connected; the last value per (topic, key) is cached and re-sent after each `sync`.

- [ ] **Step 1: Write failing tests** (`FakePeer` collects decoded messages):
  - `test_store_roundtrip`.
  - `test_store_corrupt_file_starts_empty`.
  - `test_first_hello_adopts_client_state`.
  - `test_later_hello_gets_server_state_via_sync`: the store has active `clock`, the Pi says `solid` → sync with `active_program == "clock"`.
  - `test_update_params_validates`: `{"color": "red"}` → the browser gets `error`, the Pi gets nothing.
  - `test_update_params_pushes_to_client_and_broadcasts`.
  - `test_preview_on_off_follows_browsers`.
  - `test_cached_data_resent_after_sync`.
- [ ] **Step 2: Run** `.venv/bin/pytest tests/server -v` → FAIL.
- [ ] **Step 3: Implement** `store.py`, `hub.py`.
- [ ] **Step 4: Run** → PASS.
- [ ] **Step 5: Commit** `feat: server store and hub`.

---

### Task 10: Weather service

**Files:**
- Create: `shelf_matrix/server/weather.py`
- Test: `tests/server/test_weather.py`

**Interfaces:**
- Consumes: `Hub.publish_data`, `Hub.on_params_changed`, `Store` (Task 9).
- Produces:
  - `async fetch_weather(http: httpx.AsyncClient, location: str) -> dict | None`:
    - `location` is either `"lat,lon"` (two floats) or a city name, resolved via `GET https://geocoding-api.open-meteo.com/v1/search?name=<city>&count=1`. No results → None.
    - Then `GET https://api.open-meteo.com/v1/forecast?latitude=..&longitude=..&current=temperature_2m,weather_code,is_day&daily=temperature_2m_max,temperature_2m_min&timezone=auto&forecast_days=1`.
    - Returns the weather dict defined in Task 5 (`location` = the original string; `fetched_at` = UTC ISO). HTTP/network errors raise `WeatherError`.
  - `class WeatherService(hub: Hub, http: httpx.AsyncClient, interval_s: float = 1200)`:
    - `locations() -> set[str]`: non-empty `location` params of catalog programs with `"weather" in needs`.
    - `async refresh(location)`: on `WeatherError` keep and re-publish the last good value, if any, and log. On None, publish nothing.
    - `async run()`: loop that refreshes all locations every `interval_s`. Also subscribed to `hub.on_params_changed` to refresh new locations immediately.

- [ ] **Step 1: Write failing tests** with `httpx.MockTransport`:
  - `test_fetch_by_city`: geocode JSON `{"results":[{"latitude":45.46,"longitude":9.19}]}` and forecast JSON with `current.temperature_2m=12.3, weather_code=3, is_day=0` and daily max/min arrays → the dict has `temp_c == 12.3`, `code == 3`, `is_day is False`.
  - `test_fetch_by_latlon_skips_geocoding`.
  - `test_unknown_city_yields_no_data`.
  - `test_fetch_failure_keeps_last_value`: first call OK, second returns 500 → `publish_data` is called again with the first value.
  - `test_locations_from_params`.
- [ ] **Step 2: Run** `.venv/bin/pytest tests/server/test_weather.py -v` → FAIL.
- [ ] **Step 3: Implement** `weather.py`.
- [ ] **Step 4: Run** → PASS.
- [ ] **Step 5: Commit** `feat: Open-Meteo weather service`.

---

### Task 11: Server app (HTTP + WebSockets)

**Files:**
- Create: `shelf_matrix/server/app.py`, `shelf_matrix/server/__main__.py`
- Test: `tests/server/test_app.py`

**Interfaces:**
- Consumes: `Store`, `Hub` (Task 9), `WeatherService` (Task 10).
- Produces:
  - `create_app(data_dir: Path | None = None, token: str | None = None, start_weather: bool = True) -> FastAPI`. The defaults come from env `SHELF_DATA_DIR` (default `/data`) and `SHELF_TOKEN`. A missing token raises `RuntimeError` at startup.
  - Routes:
    - `GET /` → `static/index.html`; `/static/*` → the static dir.
    - `GET /healthz` → `{"ok": true}`.
    - `WS /ws/client`: check `Authorization: Bearer <token>` with `secrets.compare_digest`; a mismatch → accept then close with code 4401. Text frames go to `on_client_message`; binary frames go to `on_client_preview` after `decode_preview`, dropping invalid ones.
    - `WS /ws/ui`: text frames go to `on_browser_message`; a `ProtocolError` → `error` reply.
  - The weather service is started in the lifespan when `start_weather` is true.
  - `python -m shelf_matrix.server` → uvicorn on `0.0.0.0:8080`.

- [ ] **Step 1: Write failing tests** with `fastapi.testclient.TestClient` and `start_weather=False`:
  - `test_client_ws_rejects_bad_token`: expect close code 4401.
  - `test_ui_receives_state_on_connect`.
  - `test_ui_select_program_reaches_client`: open both sockets; the client sends `hello` with the catalog from `load_catalog(Path("programs"))`; the UI sends `select_program demo-life` → the client receives `set_program` with `program == "demo-life"`.
  - `test_changes_while_offline_are_synced_on_reconnect`: the client connects, says hello and disconnects. The UI does `update_globals {"brightness": 15}` and receives state with `client.online == False`. The client reconnects and says hello → the `sync` has `globals.brightness == 15`.
  - `test_preview_relayed_to_ui`.
- [ ] **Step 2: Run** `.venv/bin/pytest tests/server/test_app.py -v` → FAIL.
- [ ] **Step 3: Implement** `app.py`, `__main__.py`.
- [ ] **Step 4: Run** `.venv/bin/pytest -v` (full suite) → PASS.
- [ ] **Step 5: Commit** `feat: server HTTP and WebSocket endpoints`.

---

### Task 12: Web UI (option A, one page)

**Files:**
- Create: `shelf_matrix/server/static/index.html`, `app.js`, `controls.js`, `style.css`

**Interfaces:**
- Consumes: the browser messages (Task 2) over `/ws/ui`, the binary preview, and the `state` payload (Task 9).
- Produces:
  - `controls.js` exports `createControl(spec, value, onChange) -> HTMLElement`:
    - color → `<input type=color>`;
    - choice → `<select>`;
    - bool → checkbox toggle;
    - int/float → `<input type=range>` with min/max/step and a value label;
    - text → `<input type=text maxlength>`, committed on change/blur.
    - `onChange(value)` is debounced to 150 ms for range inputs.
  - Layout, mobile-first, max-width ~480px:
    1. Header: Pi online/offline dot + last seen, and the active program's status (`crashed`/`fallback` in a warning color with its detail).
    2. Preview: a `<canvas width=64 height=64>` scaled with CSS to ~256px using `image-rendering: pixelated`, painted from binary previews (skip the 1-byte prefix and write into ImageData). Shows "no preview" when no frame has arrived for 3 s.
    3. Brightness slider (from `GLOBAL_PARAMS` in the catalog's state), always visible. Other globals go under a "more" `<details>`, which stays hidden while it's empty.
    4. Program list: the active program is highlighted. Tapping a program sends `select_program` and expands its settings inline. Settings are generated from `catalog[].params` and current values. Only one program is expanded at a time.
  - Auto-reconnect of `/ws/ui` with 1→10 s backoff, with an "offline from server" banner while disconnected.
  - The server must also include the global param specs in the `state` payload as `global_specs` (add to `Hub.state_payload`, with a test in `tests/server/test_hub.py::test_state_includes_global_specs`) so the UI never hardcodes brightness.

- [ ] **Step 1: Add the failing test** `test_state_includes_global_specs` → run → FAIL → add `global_specs` to `state_payload` → PASS.
- [ ] **Step 2: Implement** the four static files.
- [ ] **Step 3: Verify end-to-end on the PC**:
  1. Start the server: `SHELF_TOKEN=dev SHELF_DATA_DIR=$SCRATCH/data .venv/bin/python -m shelf_matrix.server`.
  2. Start a fake client: `SHELF_MATRIX_FAKE=1 .venv/bin/python -m shelf_matrix.client --config $SCRATCH/dev-config.toml`, using `server_url = ws://localhost:8080/ws/client`, `token = dev`, local `programs_dir`/`fonts_dir`, and a scratch state path.
  3. Open `http://localhost:8080` with the Playwright tools at a 390×844 viewport and check:
     - the preview shows the clock;
     - changing color updates the preview within ~1 s;
     - switching to `demo-life` shows status `fallback`, because the binary is missing on the PC, and returns to the clock;
     - brightness changes are accepted;
     - stopping the client shows the Pi offline;
     - restarting it shows online again.
  4. Take screenshots.
- [ ] **Step 4: Commit** `feat: one-page web UI`.

---

### Task 13: Server Docker image

**Files:**
- Create: `deploy/Dockerfile`, `deploy/docker-compose.yml`, `deploy/.env.example`, `.dockerignore`

**Interfaces:**
- Produces:
  - Image: `python:3.11-slim`, `pip install .[server]`, non-root user, `EXPOSE 8080`, `CMD ["python", "-m", "shelf_matrix.server"]`, and a `HEALTHCHECK` on `/healthz`.
  - Compose: service `shelf-matrix-server` with `build: {context: .., dockerfile: deploy/Dockerfile}`, `ports: ["8080:8080"]`, `env_file: .env` (`SHELF_TOKEN`), volume `shelf-data:/data`, `restart: unless-stopped`.

- [ ] **Step 1: Write the files**.
- [ ] **Step 2: Verify**:
  1. `cd deploy && cp .env.example .env && sed -i 's/^SHELF_TOKEN=.*/SHELF_TOKEN=dev/' .env && docker compose up -d --build`.
  2. `curl -s localhost:8080/healthz` → `{"ok":true}`.
  3. Repeat the fake-client check from Task 12 Step 3 against the container.
  4. `docker compose down`.
  - If Docker isn't available on this machine, say so and leave this verification to the owner's home server.
- [ ] **Step 3: Commit** `feat: Docker image and compose file for the server`.

---

### Task 14: Pi deployment + on-device verification

**Files:**
- Create: `deploy/install-pi.sh`, `deploy/shelf-matrix.service`; Modify: `README.md` (deploy section)

**Interfaces:**
- Consumes: everything above. Needs SSH access to the Pi (the owner is setting this up; ask for the host alias when this task starts).
- Produces:
  - `install-pi.sh` (idempotent, run with sudo on the Pi from the repo checkout):
    1. `apt-get install -y git python3-dev python3-venv python3-pil cython3 cmake build-essential`.
    2. Clone/update `https://github.com/hzeller/rpi-rgb-led-matrix` into `/opt/rpi-rgb-led-matrix` at pinned commit `LIB_COMMIT=3da7a3b` (overridable with env `LIB_REPO`/`LIB_COMMIT`, e.g. to use the owner's fork).
    3. `make -C examples-api-use` to build `demo`.
    4. venv `/opt/shelf-matrix/venv --system-site-packages`, then `pip install /opt/rpi-rgb-led-matrix` and `pip install /opt/shelf-matrix[client]`.
    5. Rsync the repo to `/opt/shelf-matrix`.
    6. Copy `config.example.toml` → `/etc/shelf-matrix/config.toml` only if absent.
    7. Install the unit and `systemctl enable --now shelf-matrix`.
    8. Write `/etc/modprobe.d/blacklist-rgb-matrix.conf` with `blacklist snd_bcm2835`, and set `dtparam=audio=off` in `/boot/firmware/config.txt`. Print "reboot required" if either changed.
  - Unit: `After=network-online.target` (Wants it, but the client must not depend on the network to start), `ExecStart=/opt/shelf-matrix/venv/bin/python -m shelf_matrix.client --config /etc/shelf-matrix/config.toml`, `Restart=always`, `RestartSec=2`, `User=root`, `Nice=-5`, `WorkingDirectory=/opt/shelf-matrix`.

- [ ] **Step 1: Write** `install-pi.sh`, the unit, and the README deploy section. Commit `feat: Pi installer and systemd unit`.
- [ ] **Step 2: Install on the Pi over SSH**: copy the repo and run the installer, then set `server_url` to the home server and `token` to match. Reboot. Expected: `systemctl is-active shelf-matrix` → `active`, and the clock is visible.
- [ ] **Step 3: Offline fallback**: stop the server container and reboot the Pi. Expected: the clock appears without the server and the logs show reconnect attempts. Start the server: the Pi reconnects and the UI shows it online.
- [ ] **Step 4: CPU budget**: with the clock running, record `top -b -n 5 -d 2` over SSH. Expected: the client plus the program child stays below ~15% CPU outside the matrix refresh thread. Also run with `show_refresh_rate` temporarily on: the refresh rate should stay at the 120 Hz limit. Record the numbers in `docs/device-notes.md`.
- [ ] **Step 5: Hardware pulse**: after the audio blacklist and reboot, set `disable_hardware_pulsing = false`, restart, and check that there is no error in the logs. Ask the owner to look at the panel for flicker. Keep the setting off if anything is wrong, and record the outcome in `docs/device-notes.md` and `deploy/config.example.toml`.
- [ ] **Step 6: Switch timing + looks**: time `clock → demo-life → clock` from the UI and record it. Ask the owner to photograph or describe both clock styles on the real panel, and adjust `faces.py` if anything is illegible after `Rotate:90`.
- [ ] **Step 7: Commit** `docs: device verification notes` (plus any tuning).

---

## Self-review notes

- Spec coverage:
  - autostart + offline → Tasks 6, 8, 14;
  - per-program params from manifests → Tasks 1, 12;
  - global brightness → Tasks 1, 4, 7, 12;
  - clock (both styles) → Task 5;
  - C++ demos → Task 7;
  - weather → Tasks 5, 10;
  - preview only while watched → Tasks 4, 7, 9, 12;
  - token → Tasks 8, 11;
  - room for rotation → Hub commands are the single entry point (Task 9), so a scheduler can call them;
  - room for streamed frames → `frame` reserved (Task 2);
  - fonts reuse → Task 4;
  - library as dependency → Task 14;
  - on-device checks → Task 14.
- Not in v1, per spec: rotation, public auth, RSS, r/place, streamed programs, night dimming.
