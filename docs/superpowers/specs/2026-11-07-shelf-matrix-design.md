# Shelf Matrix — Design

Date: 2026-11-07
Status: approved by owner (2026-11-07)

## Intent

A 64×64 RGB LED matrix sits on a shelf at home, driven by a Raspberry Pi Zero W 1.1
with an Adafruit RGB Matrix HAT. Most of the time it is a **clock with weather**.
It is also a **playground**: the owner wants to try other compositions cheaply —
next up an RSS ticker, later an r/place-like shared canvas that other people can
draw on.

The owner controls it from a **web app** (phone-first) on the home network today,
with the explicit intent of exposing that web app to the internet later. Only the
server may ever be exposed; the Pi must not be.

Success for v1: power on the Pi, the clock appears on its own; from a phone on
the LAN, switch programs, tweak each program's look, and change brightness —
changes visible on the matrix within about a second.

## Goals (v1)

1. New standalone repo `shelf-matrix` (placeholder name) containing server,
   Pi client, programs, deploy files and docs.
2. **Server** running in Docker on the owner's home server: hosts the web app,
   stores state, fetches weather, sends commands to the Pi.
3. **Pi client**: a systemd service that starts at boot, connects *outbound* to the
   server, runs the selected program on the matrix, and keeps working when the
   server is unreachable.
4. **Web app** in the "one page" layout (see below): live preview, global settings,
   program list with inline per-program settings.
5. **Per-program parameters** declared by each program and rendered automatically
   by the web app.
6. **Global parameters**, at minimum brightness.
7. **Clock + weather program** with two styles: digital and analog.
8. **Existing C++ demos** from the library selectable as programs, using command
   lines like the one in use today.

## Non-goals (v1) — but the design must leave room for them

- **Program rotation** (a list of programs + durations). Not built now; must be
  addable later on the server side without restructuring the client or programs.
- **Public exposure**: login, rate limiting, abuse protection, TLS termination.
  v1 is LAN-only, but nothing may assume the server and Pi are on the same network
  (the Pi connects out to a URL).
- **RSS ticker** and **r/place canvas** — first experiments after v1.
- **Server-rendered (streamed) programs**: a program that runs on the server and
  streams 64×64 frames to the Pi. Not built in v1, but the protocol reserves room
  for it (see Protocol).
- Night dimming / scheduled off. Not requested; brightness is manual in v1.

## Anti-goals (failure even if it "works")

- The shelf clock goes dark because the home server, Docker or Wi-Fi is down.
- Adding a new program or a new program setting requires editing the web app.
- Any inbound port opened on the Pi.
- Visible flicker/stutter introduced by our own software competing for the Pi's
  single CPU core.
- Mixing project code into the LED library fork (merge pain with upstream).

## Constraints

- **Hardware**: Pi Zero W 1.1 — single-core ARMv6 @ 1 GHz, 512 MB RAM, 2.4 GHz Wi-Fi.
  The matrix library's refresh thread consumes much of the core; everything else
  must stay light.
- **OS**: Raspberry Pi OS Lite (no desktop).
- **Current working matrix command** (source of default matrix options):

  ```
  sudo demo -D7 --led-gpio-mapping=adafruit-hat-pwm --led-rows=64 --led-cols=64 \
    --led-show-refresh --led-pwm-bits=9 --led-pwm-lsb-nanoseconds=50 \
    --led-limit-refresh=120 --led-pixel-mapper="Rotate:90" --led-no-hardware-pulse
  ```

  `--led-show-refresh` is a debug option and should not be in the default config.
- **Library**: `hzeller/rpi-rgb-led-matrix` (owner has a fork at
  `github.com/Roncax/rpi-rgb-led-matrix`). Used as a dependency, never vendored into
  this repo. GPL-2.0 — if the repo is published, license it GPL-2.0.
- **Matrix access requires root** at init (the library drops privileges afterwards).
  Only one process may drive the matrix at a time.
- **Home server** runs Docker; it is always-on but may be rebooted.
- **Switch latency** (owner's answer): switching programs — and changing params of
  C++ demos, which restarts them — may leave the panel dark for **a few seconds**.
  That is acceptable; crash isolation (one child process per program) wins over
  switch speed. The "about a second" target applies to live param/brightness
  changes on Python programs.
- **Device access** (owner's answer): the builder will have SSH access to the Pi
  (owner is setting it up). The plan should therefore include on-device checks:
  how the clock faces look after `Rotate:90`, CPU budget / refresh-rate impact of
  client + program, real switch timing, and whether hardware pulse works once
  `snd_bcm2835` is disabled (default to `--led-no-hardware-pulse` as today until
  verified on the device).

## Architecture

```
 phone/browser ──HTTP/WS──▶  SERVER (Docker, home server)  ◀──WS (outbound from Pi)── PI CLIENT
                             - web app (one page)                                      - supervisor (systemd)
                             - state store                                             - runs 1 program at a time
                             - weather fetcher                                         - local state cache
                             - (later) rotation, public auth                           - preview snapshots
```

### Decided by the owner

- Split into a Docker **server** and a small **Pi client**; only the server is
  ever exposed.
- Server runs on the **home server**.
- Programs are mostly **Python**.
- Web app layout **option A** (one page).
- Per-program parameters chosen in the web app (e.g. color, font); global
  parameters, starting with brightness.
- Rotation and public exposure: later, but must be possible.
- Clock comes in both **analog and digital**; first version designed by the builder.

### Pi client

- Python 3, single long-running process, run by systemd as root
  (`Restart=always`), starts at boot.
- Reads `/etc/shelf-matrix/config.toml`: server URL, client token, matrix options
  (rows, cols, gpio mapping, pwm bits, pwm lsb ns, limit refresh, pixel mapper,
  hardware pulse on/off, etc.).
- Maintains a WebSocket connection to the server with reconnect + backoff. The
  matrix keeps running regardless of connection state.
- **Runs each program as a child process**, one at a time. Switching = stop the
  current child (SIGTERM, then SIGKILL after a timeout), wait for exit, start the
  next. This isolates crashes and lets C++ demos run unchanged. If a child crashes
  it is restarted with backoff; after repeated crashes the client falls back to the
  clock program.
- **Local state cache** (`/var/lib/shelf-matrix/state.json`): active program,
  per-program params, global params, last weather data. On boot the client starts
  the cached program immediately, before contacting the server. If there is no
  cache, it starts the clock.
- Talks to its Python child over a local channel (stdin/stdout JSON lines or a
  Unix socket — builder's choice) to push param changes, global changes and data
  (e.g. weather) **live, without restarting** the program, and to receive preview
  snapshots.
- C++ demos cannot take live updates: param or brightness changes restart them
  with new flags (`--led-brightness`, etc.).

### Programs

- One folder per program under `programs/`, containing a **manifest** and the
  code. The manifest declares:
  - id, display name, kind (`python` or `command`)
  - for `command`: the command line template (matrix options injected from config)
  - **parameter schema**: list of params, each with name, label, type, default and
    type-specific constraints. Types needed in v1: `color`, `choice`
    (incl. font list), `bool`, `int`/`float` with min/max (slider), `text`.
- A small shared Python helper (`shelf_matrix.program` or similar) that every
  Python program uses: creates the matrix from config, exposes current params and
  globals, delivers live updates and data, and handles preview snapshots. Writing a
  new program = a manifest plus one class/function that draws a frame; nothing else.
- The Pi client reports the catalog (manifests) to the server on connect. The
  **server does not need program code**; it learns programs and schemas from the
  client.
- Fonts: reuse the library's BDF fonts (copied/installed from the library), so the
  `choice` font param has a sensible list.

### Clock + weather program (v1, designed by builder)

- Param `style`: `digital` | `analog`.
- **Digital**: large time centered, date + weather strip (icon, current temp,
  optionally today's min/max).
- **Analog**: round face filling most of the 64×64, hour/minute hands, optional
  seconds hand, compact weather corner.
- Other params (builder may refine): primary/accent colors, font (digital),
  24h/12h, show seconds, weather location (city name or lat/lon).
- Weather: rendered from data pushed by the client (originating at the server).
  If no data ever received: show dashes. If stale: keep last values (builder may
  add a subtle "stale" hint).
- Must look good on the actual panel at 64×64 after `Rotate:90`; redraw at ~1 Hz
  (or per-second tick for seconds hand) — no needless CPU use.

### Server

- Python (FastAPI or similar async framework), single container,
  `docker-compose.yml` included. Persistent volume for state.
- State store: per-program params, global params, active program, client info.
  SQLite or a JSON file — builder's choice.
- **Weather fetcher**: Open-Meteo (free, no API key), every 15–30 min, for the
  location(s) configured in programs that need it; pushes data to the client.
- Serves the web app and a browser WebSocket (or SSE) for live state + preview.
- Is the source of truth for settings; pushes the full relevant state to the Pi on
  every (re)connect so they converge.
- Structured so rotation can be added as a server-side scheduler that issues the
  same "switch program" commands the UI does.

### Web app (option A, one page, phone-first)

- **Top**: live preview of the matrix (scaled-up 64×64), brightness slider always
  visible; other global settings behind a "more" toggle.
- **Below**: program list, active program highlighted. Tapping a program switches
  the matrix to it and expands its settings inline. Controls are generated from the
  parameter schema.
- **Changes apply immediately** (no Save button), debounced for sliders. Settings
  are remembered per program, so switching back restores them.
- Shows connection status of the Pi (online / offline, last seen).
- Programs without preview support (C++ demos) show a "no preview" placeholder.
- No login in v1.

### Protocol (Pi ⇄ server)

- WebSocket, Pi connects to `wss?://<server>/ws/client` with a shared token in a
  header — cheap now, needed once exposed.
- JSON messages, versioned. Builder defines exact shapes; the needed message
  kinds:
  - Pi → server: `hello` (client version, program catalog with schemas, current
    state), `status` (active program, running/crashed), `preview` (64×64 RGB
    snapshot, ~1 fps max, only while at least one browser is watching).
  - Server → Pi: `set_program` (id + params), `set_params`, `set_globals`,
    `data` (e.g. weather payload keyed by topic), `preview_on/off`.
  - Reserved for later: `frame` (server-streamed 64×64 frames, for server-rendered
    programs and possibly r/place).

### Deploy

- `deploy/` contains: Pi install script (apt deps, build + install the library's
  Python bindings from upstream at a pinned commit, or from the owner's fork if a
  patch is ever needed; venv; config template; systemd unit), and the server's
  `docker-compose.yml`.
- Install script should also disable the onboard audio driver (`snd_bcm2835`) so
  hardware pulse can be used; the config keeps a switch for
  `--led-no-hardware-pulse` in case it's still needed.
- Updating the Pi: `git pull` + rerun install / restart service. No CI/CD needed
  in v1.

## Repo layout (suggested)

```
shelf-matrix/
  server/      FastAPI app, web UI assets, Dockerfile
  client/      Pi supervisor service
  programs/    one folder per program (manifest + code)
  shared/      protocol definitions / program helper (if shared)
  deploy/      install script, systemd unit, config example, docker-compose.yml
  docs/
```

## Left to the builder

- Exact framework choices within Python (FastAPI vs alternatives, frontend with
  plain JS vs a tiny framework — keep it light), SQLite vs JSON store.
- Exact message schemas, manifest file format (TOML/JSON/YAML).
- Child-process IPC mechanism.
- Visual design of the clock faces and the web page within the constraints above.
- Which C++ demos to list by default (at least the `-D7` one in use today).
- Testing approach: logic (protocol, state, schema→controls, supervisor) should be
  testable off-Pi with a fake matrix; the rgbmatrix import must be swappable for an
  emulator/fake so programs can be developed on a PC.
