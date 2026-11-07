// Shelf Matrix one-page UI: talks to the server over /ws/ui.
// The server's `state` broadcast is the source of truth; local edits are
// applied optimistically and then confirmed (or corrected) by it.
import { createControl } from "./controls.js";

const PROTOCOL_VERSION = 1;
const PREVIEW_PREFIX = 0x01;
const PREVIEW_SIZE = 64;
const PREVIEW_BYTES = PREVIEW_SIZE * PREVIEW_SIZE * 3;
const PREVIEW_STALE_MS = 3000;
const BACKOFF_MIN_MS = 1000;
const BACKOFF_MAX_MS = 10000;

const $ = (id) => document.getElementById(id);

let ws = null;
let backoff = BACKOFF_MIN_MS;
let state = null;            // last `state` payload from the server
let structureKey = null;     // catalog + global specs the DOM was built from
// Which program's settings are unfolded: a program id, FOLDED (the user
// deliberately folded everything), or null (not decided yet: follow the
// active program).
const FOLDED = Symbol("folded");
let expanded = null;
let lastActive;              // active_program seen by the last render
let warnedOffline = false;   // "not connected" toast shown for this disconnect
const controls = [];         // { values: () => object, name, el }
const programEls = new Map(); // program id -> { root, button, panel }

// ----- connection -----------------------------------------------------------

function send(kind, payload) {
  if (ws && ws.readyState === WebSocket.OPEN) {
    ws.send(JSON.stringify({ v: PROTOCOL_VERSION, kind, ...payload }));
  } else if (!warnedOffline) {
    warnedOffline = true;  // the banner keeps explaining it after this
    toast("Not connected to the server");
  }
}

function connect() {
  const proto = location.protocol === "https:" ? "wss:" : "ws:";
  const base = location.pathname.replace(/[^/]*$/, "");
  ws = new WebSocket(`${proto}//${location.host}${base}ws/ui`);
  ws.binaryType = "arraybuffer";
  ws.onopen = () => {
    backoff = BACKOFF_MIN_MS;
    warnedOffline = false;
    $("server-banner").hidden = true;
  };
  ws.onmessage = (ev) => {
    if (typeof ev.data === "string") onText(ev.data);
    else onPreview(new Uint8Array(ev.data));
  };
  ws.onclose = () => {
    ws = null;
    $("server-banner").hidden = false;
    // The Pi's state is unknown until the server is back.
    $("pi").classList.remove("online");
    setText($("pi-text"), "Pi \u2014");
    $("pi-seen").textContent = "";
    $("status").hidden = true;
    setTimeout(connect, backoff);
    backoff = Math.min(backoff * 2, BACKOFF_MAX_MS);
  };
}

function onText(text) {
  let msg;
  try {
    msg = JSON.parse(text);
  } catch {
    return;
  }
  if (msg.kind === "state") {
    state = msg;
    render();
  } else if (msg.kind === "error") {
    toast(msg.message || "Error");
  }
}

// ----- preview --------------------------------------------------------------

const canvas = $("preview");
const ctx = canvas.getContext("2d");
const image = ctx.createImageData(PREVIEW_SIZE, PREVIEW_SIZE);
let lastFrameAt = -Infinity;

function onPreview(bytes) {
  if (bytes.length !== PREVIEW_BYTES + 1 || bytes[0] !== PREVIEW_PREFIX) return;
  const px = image.data;
  for (let i = 0, j = 1; i < px.length; i += 4, j += 3) {
    px[i] = bytes[j];
    px[i + 1] = bytes[j + 1];
    px[i + 2] = bytes[j + 2];
    px[i + 3] = 255;
  }
  ctx.putImageData(image, 0, 0);
  lastFrameAt = performance.now();
  $("no-preview").hidden = true;
}

setInterval(() => {
  const stale = performance.now() - lastFrameAt > PREVIEW_STALE_MS;
  $("no-preview").hidden = !stale;
  canvas.classList.toggle("stale", stale);
}, 500);

// ----- rendering ------------------------------------------------------------

function render() {
  const key = JSON.stringify([state.catalog, state.global_specs]);
  if (key !== structureKey) {
    structureKey = key;
    build();
  }
  // Follow the active program when it changes (here or in another browser),
  // unless the user deliberately folded everything.
  const activeChanged = state.active_program !== lastActive;
  lastActive = state.active_program;
  if (expanded === null ||
      (expanded !== FOLDED && (activeChanged || !programEls.has(expanded)))) {
    expanded = state.active_program ?? null;
  }
  update();
}

/** (Re)build the global controls and the program list from the specs. */
/** createControl, or a placeholder row for a spec this UI can't render. */
function safeControl(spec, value, onChange) {
  try {
    return createControl(spec, value, onChange);
  } catch (e) {
    console.warn(e);
    const el = document.createElement("div");
    el.className = "control control-unsupported";
    el.textContent = `${spec.label ?? spec.name}: unsupported setting`;
    el.setValue = () => {};
    el.isBusy = () => false;
    return el;
  }
}

function build() {
  controls.length = 0;
  programEls.clear();

  const main = $("globals-main");
  const moreBody = $("globals-more-body");
  main.replaceChildren();
  moreBody.replaceChildren();
  for (const spec of state.global_specs || []) {
    const el = safeControl(spec, state.globals[spec.name], (value) => {
      state.globals[spec.name] = value;
      send("update_globals", { globals: { [spec.name]: value } });
    });
    controls.push({ values: () => state.globals, name: spec.name, el });
    if (spec.name === "brightness") {
      el.classList.add("brightness");
      const sun = document.createElement("span");
      sun.className = "sun";
      sun.setAttribute("aria-hidden", "true");
      sun.textContent = "\u2600";
      el.querySelector(".control-label")?.prepend(sun);
      main.append(el);
    } else {
      moreBody.append(el);
    }
  }
  $("globals-more").hidden = moreBody.childElementCount === 0;

  const list = $("programs");
  list.replaceChildren();
  for (const m of state.catalog) {
    const root = document.createElement("section");
    root.className = "program";

    const button = document.createElement("button");
    button.type = "button";
    button.className = "program-head";
    button.id = `prog-${m.id}`;
    button.setAttribute("aria-controls", `panel-${m.id}`);
    button.innerHTML = '<span class="led" aria-hidden="true"></span>' +
      '<span class="program-name"></span><span class="program-tag"></span>' +
      '<span class="chevron" aria-hidden="true"></span>';
    button.querySelector(".program-name").textContent = m.name;
    button.addEventListener("click", () => onProgramTap(m.id));

    const panel = document.createElement("div");
    panel.className = "program-panel";
    panel.id = `panel-${m.id}`;
    panel.setAttribute("role", "region");
    panel.setAttribute("aria-labelledby", button.id);
    if (m.params.length === 0) {
      const p = document.createElement("p");
      p.className = "empty";
      p.textContent = "No settings";
      panel.append(p);
    }
    for (const spec of m.params) {
      const values = state.params[m.id] || {};
      const el = safeControl(spec, values[spec.name], (value) => {
        (state.params[m.id] ||= {})[spec.name] = value;
        send("update_params", { program: m.id, params: { [spec.name]: value } });
      });
      controls.push({ values: () => state.params[m.id], name: spec.name, el });
      panel.append(el);
    }

    root.append(button, panel);
    list.append(root);
    programEls.set(m.id, { root, button, panel });
  }
  if (state.catalog.length === 0) {
    const p = document.createElement("p");
    p.className = "empty";
    p.textContent = "No programs yet — waiting for the Pi to connect.";
    list.append(p);
  }
}

function onProgramTap(id) {
  if (id !== state.active_program) {
    state.active_program = id;     // optimistic; the next `state` confirms it
    lastActive = id;
    expanded = id;
    send("select_program", { program: id });
  } else {
    expanded = expanded === id ? FOLDED : id;
  }
  update();
}

/** Push current values and status into the existing DOM. */
function update() {
  for (const { values: get, name, el } of controls) {
    if (el.isBusy()) continue;
    const values = get();
    if (values && name in values) el.setValue(values[name]);
  }

  // In fallback the status names the program actually running (the
  // fallback), so it is matched to the selected program by state only.
  const status = state.client.online ? state.client.status : null;
  for (const [id, { root, button, panel }] of programEls) {
    const active = id === state.active_program;
    const open = id === expanded;
    root.classList.toggle("active", active);
    root.classList.toggle("open", open);
    button.setAttribute("aria-expanded", String(open));
    button.setAttribute("aria-current", active ? "true" : "false");
    panel.hidden = !open;
    const tag = button.querySelector(".program-tag");
    const problem = active && status &&
      (status.state === "crashed" || status.state === "fallback");
    tag.textContent = problem ? status.state : active ? "on" : "";
    tag.classList.toggle("warn", Boolean(problem));
  }

  renderHeader();
}

/** Set text only when it changes, so live regions don't re-announce. */
function setText(el, text) {
  if (el.textContent !== text) el.textContent = text;
}

function nameOf(id) {
  const m = state.catalog.find((p) => p.id === id);
  return m ? m.name : id;
}

function ago(iso) {
  if (!iso) return "never";
  const s = Math.max(0, Math.round((Date.now() - Date.parse(iso)) / 1000));
  if (s < 60) return `${s}s ago`;
  if (s < 3600) return `${Math.floor(s / 60)} min ago`;
  if (s < 86400) return `${Math.floor(s / 3600)} h ago`;
  return new Date(iso).toLocaleString();
}

function renderHeader() {
  if (!state || !ws || ws.readyState !== WebSocket.OPEN) return;
  const { online, last_seen: lastSeen, status } = state.client;
  $("pi").classList.toggle("online", online);
  // Only #pi-text is a live region: it changes on online/offline, not on
  // every tick of the "seen … ago" counter.
  setText($("pi-text"), online ? "Pi online" : "Pi offline");
  $("pi-seen").textContent = online ? "" : `· seen ${ago(lastSeen)}`;

  const el = $("status");
  if (!online || !status) {
    el.hidden = true;
    return;
  }
  const warn = status.state === "crashed" || status.state === "fallback";
  let text = `${nameOf(status.active_program)} · ${status.state}`;
  if (status.detail) text += ` — ${status.detail}`;
  setText(el, text);
  el.classList.toggle("warn", warn);
  el.hidden = false;
}

setInterval(renderHeader, 10000);  // keep "seen … ago" fresh

// ----- toast ----------------------------------------------------------------

let toastTimer = null;
function toast(message) {
  const el = $("toast");
  el.textContent = message;
  el.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { el.hidden = true; }, 4000);
}

connect();
