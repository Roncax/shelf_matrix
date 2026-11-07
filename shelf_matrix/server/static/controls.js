// Form controls generated from a ParamSpec dict (see shelf_matrix/schema.py).
//
// createControl(spec, value, onChange) returns a labelled row element with two
// extra methods used by app.js:
//   el.setValue(v)  show a value coming from the server
//   el.isBusy()     true while the user is dragging/typing, so a server
//                   broadcast must not overwrite what they are doing

const DEBOUNCE_MS = 150;
const PLACEHOLDERS = { location: "City or lat,lon" };

let nextId = 0;

function debounce(fn, ms) {
  let timer = null;
  let lastArgs = null;
  const run = () => {
    timer = null;
    fn(...lastArgs);
  };
  const call = (...args) => {
    lastArgs = args;
    clearTimeout(timer);
    timer = setTimeout(run, ms);
  };
  call.flush = () => {
    if (timer !== null) {
      clearTimeout(timer);
      run();
    }
  };
  call.pending = () => timer !== null;
  return call;
}

function decimals(step) {
  const s = String(step ?? 1);
  return s.includes(".") ? s.split(".")[1].length : 0;
}

function row(spec, input, extra) {
  const el = document.createElement("div");
  el.className = `control control-${spec.type}`;
  const label = document.createElement("label");
  label.className = "control-label";
  label.htmlFor = input.id;
  label.textContent = spec.label;
  el.append(label);
  if (extra) el.append(extra);
  el.append(input);
  return el;
}

function rangeControl(spec, value, onChange) {
  const isInt = spec.type === "int";
  const step = spec.step ?? (isInt ? 1 : 0.1);
  const input = document.createElement("input");
  input.type = "range";
  input.id = `ctl-${nextId++}`;
  input.min = spec.min ?? 0;
  input.max = spec.max ?? 100;
  input.step = step;
  const out = document.createElement("output");
  out.className = "control-value";
  out.htmlFor = input.id;
  const places = decimals(step);
  const parse = () => (isInt ? parseInt(input.value, 10) : parseFloat(input.value));
  const show = () => {
    out.textContent = parse().toFixed(isInt ? 0 : places);
    const span = input.max - input.min || 1;
    input.style.setProperty("--fill", `${((input.value - input.min) / span) * 100}%`);
  };
  const send = debounce(onChange, DEBOUNCE_MS);
  let pointerDown = false;
  const release = () => { pointerDown = false; };
  // Listen on window: the pointer may be released outside the slider.
  input.addEventListener("pointerdown", () => {
    pointerDown = true;
    window.addEventListener("pointerup", release, { once: true });
    window.addEventListener("pointercancel", release, { once: true });
  });
  input.addEventListener("input", () => { show(); send(parse()); });
  input.addEventListener("change", () => { release(); send.flush(); });

  const el = row(spec, input, out);
  el.setValue = (v) => { input.value = v; show(); };
  el.isBusy = () => pointerDown || send.pending();
  el.setValue(value);
  return el;
}

function colorControl(spec, value, onChange) {
  const input = document.createElement("input");
  input.type = "color";
  input.id = `ctl-${nextId++}`;
  const swatch = document.createElement("span");
  swatch.className = "control-value mono";
  // Live feedback while the picker is open, without flooding the Pi.
  const send = debounce(onChange, DEBOUNCE_MS);
  input.addEventListener("input", () => { swatch.textContent = input.value; send(input.value); });
  input.addEventListener("change", () => send.flush());
  const el = row(spec, input, swatch);
  el.setValue = (v) => { input.value = v; swatch.textContent = v; };
  el.isBusy = () => send.pending();
  el.setValue(value);
  return el;
}

function choiceControl(spec, value, onChange) {
  const select = document.createElement("select");
  select.id = `ctl-${nextId++}`;
  for (const opt of spec.options) {
    const o = document.createElement("option");
    o.value = opt;
    o.textContent = opt;
    select.append(o);
  }
  select.addEventListener("change", () => onChange(select.value));
  const el = row(spec, select);
  el.setValue = (v) => { select.value = v; };
  el.isBusy = () => false;
  el.setValue(value);
  return el;
}

function boolControl(spec, value, onChange) {
  const input = document.createElement("input");
  input.type = "checkbox";
  input.id = `ctl-${nextId++}`;
  input.setAttribute("role", "switch");
  input.className = "switch";
  input.addEventListener("change", () => onChange(input.checked));
  const el = row(spec, input);
  el.setValue = (v) => { input.checked = Boolean(v); };
  el.isBusy = () => false;
  el.setValue(value);
  return el;
}

function textControl(spec, value, onChange) {
  const input = document.createElement("input");
  input.type = "text";
  input.id = `ctl-${nextId++}`;
  input.maxLength = spec.max_len ?? 200;
  input.autocomplete = "off";
  input.spellcheck = false;
  input.enterKeyHint = "done";
  if (PLACEHOLDERS[spec.name]) input.placeholder = PLACEHOLDERS[spec.name];
  let committed = value;
  // `change` fires on blur and on Enter: one commit per edit.
  input.addEventListener("change", () => {
    if (input.value !== committed) {
      committed = input.value;
      onChange(input.value);
    }
  });
  input.addEventListener("keydown", (e) => { if (e.key === "Enter") input.blur(); });
  const el = row(spec, input);
  el.setValue = (v) => { committed = v; input.value = v; };
  el.isBusy = () => document.activeElement === input;
  el.setValue(value);
  return el;
}

const BUILDERS = {
  color: colorControl,
  choice: choiceControl,
  bool: boolControl,
  int: rangeControl,
  float: rangeControl,
  text: textControl,
};

export function createControl(spec, value, onChange) {
  const build = BUILDERS[spec.type];
  if (!build) throw new Error(`unknown param type ${spec.type}`);
  return build(spec, value ?? spec.default, onChange);
}
