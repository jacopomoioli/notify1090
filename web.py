#!/usr/bin/env python3
"""notify1090 web UI — notification history + config editor"""
import argparse
import json
import sqlite3
import http.server
import urllib.request

DB_PATH = "notify1090.db"
PLANESPOTTERS_URL = "https://api.planespotters.net/pub/photos/hex/{hex}"
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36"

CONFIG_FIELDS = [
    # (key, label, input_type, description, section)
    ("tar1090_url",           "tar1090 URL",             "text",     "Base URL of your tar1090 instance (e.g. http://192.168.1.10:8080)",             "Connection"),
    ("latitude",              "Latitude",                "number",   "Your location latitude in decimal degrees",                                      "Location"),
    ("longitude",             "Longitude",               "number",   "Your location longitude in decimal degrees",                                     "Location"),
    ("radius_km",             "Radius (km)",             "number",   "Only aircraft within this radius will be considered",                            "Location"),
    ("poll_interval_seconds", "Poll interval (s)",       "number",   "How often to query tar1090",                                                     "Polling"),
    ("seen_ttl_hours",        "Seen TTL (hours)",        "number",   "Hours before a previously seen aircraft is re-evaluated",                        "Polling"),
    ("exclude_type_regex",    "Exclude type regex",      "text",     "Regex matched against the ICAO type code to skip before LLM (e.g. ^(B738|A320)$)", "Filtering"),
    ("prompt",                "LLM prompt",              "textarea", "Sent to the LLM to decide if the aircraft is interesting. Must end with the YES/NO instruction.", "LLM"),
    ("openrouter_api_key",    "OpenRouter API key",      "password", "OpenRouter API key. Leave blank to disable LLM filtering.",                      "LLM"),
    ("openrouter_model",      "OpenRouter model",        "text",     "Model ID (e.g. google/gemini-2.5-flash). Defaults to google/gemini-2.5-flash if left blank.", "LLM"),
    ("telegram_bot_token",    "Telegram bot token",      "password", "From @BotFather. Leave blank if using ntfy.",                                    "Notifications"),
    ("telegram_chat_id",      "Telegram chat ID",        "text",     "Your numeric chat ID from @userinfobot.",                                        "Notifications"),
    ("ntfy_topic",            "ntfy topic",              "text",     "ntfy.sh topic name. Leave blank if using Telegram.",                             "Notifications"),
    ("screenshot",            "Enable screenshots",      "checkbox", "Take a tar1090 map screenshot on each notification (requires playwright + pillow).", "Screenshots"),
    ("screenshot_params",     "Screenshot query string", "text",     "Query string appended to the tar1090 URL for screenshots.",                      "Screenshots"),
    ("screenshot_viewport",   "Viewport width (px)",     "number",   "Width of the screenshot viewport.",                                              "Screenshots"),
]


def _db_connect(db_path):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def _load_notifications(db_path):
    try:
        conn = _db_connect(db_path)
        rows = conn.execute("""
            SELECT id, timestamp, type, hex, callsign, reg, aircraft_type,
                   distance_km, altitude, reason, squawk,
                   origin_iata, destination_iata, airline
            FROM notifications ORDER BY timestamp DESC
        """).fetchall()
        conn.close()
        return [dict(r) for r in rows]
    except sqlite3.OperationalError:
        return []


def _load_config(db_path):
    try:
        conn = _db_connect(db_path)
        rows = conn.execute("SELECT key, value FROM config").fetchall()
        conn.close()
        conf = {}
        for key, value in rows:
            try:
                conf[key] = json.loads(value)
            except (json.JSONDecodeError, ValueError):
                conf[key] = value
        return conf
    except sqlite3.OperationalError:
        return {}


def _save_config(db_path, updates):
    conn = _db_connect(db_path)
    for key, value in updates.items():
        conn.execute(
            "INSERT OR REPLACE INTO config (key, value) VALUES (?, ?)",
            (key, json.dumps(value))
        )
    conn.commit()
    conn.close()


def _fetch_planespotters(hex_code):
    try:
        req = urllib.request.Request(
            PLANESPOTTERS_URL.format(hex=hex_code),
            headers={"User-Agent": UA}
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode())
        photos = data.get("photos")
        if photos:
            return photos[0]["thumbnail_large"]["src"]
    except Exception:
        pass
    return None


HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>notify1090</title>
<style>
*, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
       background: #0d1117; color: #c9d1d9; min-height: 100vh; font-size: 14px; }

/* ── Header / tabs ── */
.topbar { background: #161b22; border-bottom: 1px solid #21262d;
          padding: 0 1.5rem; display: flex; align-items: center; gap: 2rem; height: 52px; }
.topbar h1 { font-size: 1rem; font-weight: 600; color: #f0f6fc; white-space: nowrap; }
.tabs { display: flex; }
.tab-btn { background: none; border: none; border-bottom: 2px solid transparent;
           color: #8b949e; padding: 0 1rem; height: 52px; cursor: pointer;
           font-size: 0.88rem; transition: color .15s; }
.tab-btn:hover { color: #c9d1d9; }
.tab-btn.active { color: #f0f6fc; border-bottom-color: #f78166; }

/* ── Layout ── */
.page { padding: 1.5rem; max-width: 1600px; }
.tab-panel { display: none; }
.tab-panel.active { display: block; }

/* ── Stats row ── */
.stats { display: flex; gap: .75rem; margin-bottom: 1rem; flex-wrap: wrap; }
.stat { background: #161b22; border: 1px solid #21262d; border-radius: 6px;
        padding: .6rem 1.1rem; flex: 1; min-width: 110px; }
.stat .val { font-size: 1.6rem; font-weight: 700; line-height: 1; }
.stat .lbl { font-size: .7rem; color: #8b949e; margin-top: 2px; text-transform: uppercase; letter-spacing: .05em; }
.stat.notify .val  { color: #56d364; }
.stat.skip .val    { color: #f85149; }
.stat.exclude .val { color: #8b949e; }
.stat.emergency .val { color: #f0b429; }

/* ── Filter bar ── */
.filter-bar { background: #161b22; border: 1px solid #21262d; border-radius: 6px;
              padding: .65rem 1rem; display: flex; gap: 1rem; align-items: center;
              flex-wrap: wrap; margin-bottom: 1rem; }
.filter-bar label { display: flex; align-items: center; gap: .35rem;
                    font-size: .82rem; cursor: pointer; user-select: none; }
.filter-bar input[type=checkbox] { accent-color: #58a6ff; }
.sep { width: 1px; height: 1.1rem; background: #30363d; }
.search-wrap { display: flex; align-items: center; gap: .4rem; }
.search-wrap input { background: #0d1117; border: 1px solid #30363d; color: #c9d1d9;
                     border-radius: 5px; padding: .3rem .6rem; font-size: .82rem; width: 220px; }
.search-wrap input:focus { outline: none; border-color: #58a6ff; }
.entry-count { font-size: .78rem; color: #8b949e; margin-left: auto; }

/* ── Badges ── */
.badge { display: inline-block; padding: .12rem .48rem; border-radius: 3px;
         font-size: .7rem; font-weight: 700; letter-spacing: .04em; }
.b-NOTIFY    { background: #1a4731; color: #56d364; border: 1px solid #238636; }
.b-SKIP      { background: #3d1a1a; color: #f85149; border: 1px solid #da3633; }
.b-EXCLUDE   { background: #21262d; color: #8b949e; border: 1px solid #30363d; }
.b-EMERGENCY { background: #5a4000; color: #f0b429; border: 1px solid #d29922; }

/* ── Table ── */
.tbl-wrap { border: 1px solid #21262d; border-radius: 6px; overflow-x: auto; }
table { width: 100%; border-collapse: collapse; font-size: .8rem; }
thead tr { background: #161b22; }
thead th { position: sticky; top: 0; background: #161b22; z-index: 2;
           padding: .55rem .7rem; text-align: left; white-space: nowrap;
           color: #8b949e; font-weight: 500; font-size: .68rem;
           text-transform: uppercase; letter-spacing: .05em;
           border-bottom: 1px solid #21262d; }
tbody tr { border-bottom: 1px solid #21262d; transition: background .1s; }
tbody tr:last-child { border-bottom: none; }
tbody tr:hover { background: #161b22; }
td { padding: .45rem .7rem; vertical-align: middle; white-space: nowrap; }
td.reason-cell { white-space: normal; max-width: 280px; color: #8b949e; font-size: .76rem; }
td.route-cell  { color: #8b949e; font-size: .76rem; }
a { color: #58a6ff; text-decoration: none; }
a:hover { text-decoration: underline; }

/* photo cell */
td.photo-cell { width: 88px; padding: .25rem .5rem; }
td.photo-cell img { display: block; height: 60px; border-radius: 3px; cursor: zoom-in; }
td.photo-cell button { background: #21262d; border: 1px solid #30363d; color: #8b949e;
                        border-radius: 3px; padding: .22rem .45rem; font-size: .68rem; cursor: pointer; }
td.photo-cell button:hover { border-color: #58a6ff; color: #58a6ff; }
td.photo-cell .ph-miss { color: #484f58; font-size: .68rem; }

/* time */
.t-rel  { display: block; }
.t-abs  { display: block; font-size: .7rem; color: #8b949e; margin-top: 1px; }

/* empty state */
.empty { text-align: center; padding: 3rem 1rem; color: #8b949e; }

/* ── Config ── */
.cfg-layout { display: flex; gap: 2rem; align-items: flex-start; }
.cfg-form   { flex: 1; max-width: 700px; }
.cfg-section { margin-bottom: 2rem; }
.cfg-section h2 { font-size: .75rem; font-weight: 600; color: #8b949e;
                  text-transform: uppercase; letter-spacing: .07em;
                  margin-bottom: .9rem; padding-bottom: .45rem;
                  border-bottom: 1px solid #21262d; }
.field { margin-bottom: 1.1rem; }
.field > label { display: block; font-size: .85rem; font-weight: 500;
                 color: #c9d1d9; margin-bottom: .25rem; }
.field .desc { font-size: .73rem; color: #8b949e; margin-bottom: .35rem; line-height: 1.4; }
.field input[type=text],
.field input[type=number],
.field input[type=password],
.field textarea {
    width: 100%; background: #0d1117; border: 1px solid #30363d; color: #c9d1d9;
    border-radius: 6px; padding: .45rem .7rem; font-size: .85rem; font-family: inherit;
}
.field textarea { min-height: 110px; resize: vertical; font-family: ui-monospace, monospace; line-height: 1.5; }
.field input:focus, .field textarea:focus { outline: none; border-color: #58a6ff; box-shadow: 0 0 0 3px rgba(88,166,255,.12); }
.cb-row { display: flex; align-items: center; gap: .5rem; }
.cb-row input[type=checkbox] { accent-color: #58a6ff; width: 15px; height: 15px; }
.cb-row label { font-size: .85rem; font-weight: 500; color: #c9d1d9; cursor: pointer; }

.save-bar { position: sticky; bottom: 0; background: #161b22; border-top: 1px solid #21262d;
            padding: .85rem 1.5rem; display: flex; align-items: center; gap: 1rem;
            margin: 0 -1.5rem; }
.btn-primary { background: #238636; color: #fff; border: 1px solid #2ea043;
               border-radius: 6px; padding: .45rem 1.2rem; font-size: .88rem;
               font-weight: 500; cursor: pointer; }
.btn-primary:hover { background: #2ea043; }
.save-msg { font-size: .82rem; }
.save-msg.ok  { color: #56d364; }
.save-msg.err { color: #f85149; }
</style>
</head>
<body>

<div class="topbar">
  <h1>✈ notify1090</h1>
  <nav class="tabs">
    <button class="tab-btn active" onclick="switchTab('notifications',this)">Notifications</button>
    <button class="tab-btn"        onclick="switchTab('settings',this)">Settings</button>
  </nav>
</div>

<div class="page">

<!-- ══════════════ NOTIFICATIONS ══════════════ -->
<div id="tab-notifications" class="tab-panel active">
  <div class="stats" id="stats"></div>
  <div class="filter-bar">
    <label><input type="checkbox" data-type="NOTIFY"    checked> Notify</label>
    <label><input type="checkbox" data-type="SKIP"      checked> Skip</label>
    <label><input type="checkbox" data-type="EXCLUDE">           Exclude</label>
    <label><input type="checkbox" data-type="EMERGENCY" checked> Emergency</label>
    <div class="sep"></div>
    <div class="search-wrap">
      <svg width="13" height="13" viewBox="0 0 16 16" fill="#8b949e"><path d="M11.742 10.344a6.5 6.5 0 1 0-1.397 1.398h-.001c.03.04.062.078.098.115l3.85 3.85a1 1 0 0 0 1.415-1.414l-3.85-3.85a1.007 1.007 0 0 0-.115-.099zm-5.242 1.156a5.5 5.5 0 1 1 0-11 5.5 5.5 0 0 1 0 11z"/></svg>
      <input type="text" id="search" placeholder="callsign, reg, hex, airline…">
    </div>
    <span class="entry-count" id="entry-count"></span>
  </div>
  <div class="tbl-wrap">
    <table>
      <thead>
        <tr>
          <th>Photo</th>
          <th>Time</th>
          <th>Type</th>
          <th>Hex</th>
          <th>Callsign</th>
          <th>Reg</th>
          <th>Aircraft</th>
          <th>Route</th>
          <th>Dist</th>
          <th>Alt</th>
          <th>Reason / Squawk</th>
        </tr>
      </thead>
      <tbody id="tbody"></tbody>
    </table>
    <div class="empty" id="empty" style="display:none">No entries yet — start notify1090.py to begin tracking aircraft.</div>
  </div>
</div>

<!-- ══════════════ SETTINGS ══════════════ -->
<div id="tab-settings" class="tab-panel">
  <div class="cfg-layout">
    <form class="cfg-form" id="cfg-form" onsubmit="return false"></form>
  </div>
  <div class="save-bar">
    <button class="btn-primary" onclick="saveConfig()">Save Settings</button>
    <span class="save-msg" id="save-msg"></span>
  </div>
</div>

</div><!-- .page -->

<script>
// ─── Tab switching ───────────────────────────────────────────────────────────
function switchTab(name, btn) {
  document.querySelectorAll('.tab-panel').forEach(p => p.classList.remove('active'));
  document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
  document.getElementById('tab-' + name).classList.add('active');
  btn.classList.add('active');
}

// ─── Notifications ───────────────────────────────────────────────────────────
let DATA = [];
const photoCache = {};

function relTime(ts) {
  const d = Math.floor(Date.now() / 1000) - ts;
  if (d < 60)    return d + 's ago';
  if (d < 3600)  return Math.floor(d / 60) + 'm ago';
  if (d < 86400) return Math.floor(d / 3600) + 'h ago';
  return Math.floor(d / 86400) + 'd ago';
}
function absTime(ts) { return new Date(ts * 1000).toLocaleString(); }
function esc(s) {
  return String(s ?? '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}

function render() {
  const checked = new Set([...document.querySelectorAll('.filter-bar input[type=checkbox]:checked')].map(e => e.dataset.type));
  const q = document.getElementById('search').value.toLowerCase().trim();
  const filtered = DATA.filter(e =>
    checked.has(e.type) &&
    (!q || [e.hex, e.callsign, e.reg, e.aircraft_type, e.airline, e.origin_iata, e.destination_iata]
            .some(v => v && String(v).toLowerCase().includes(q)))
  );
  document.getElementById('entry-count').textContent = filtered.length + ' / ' + DATA.length + ' entries';
  const tbody = document.getElementById('tbody');
  const empty = document.getElementById('empty');
  if (filtered.length === 0) { tbody.innerHTML = ''; empty.style.display = ''; return; }
  empty.style.display = 'none';
  tbody.innerHTML = filtered.map(e => {
    const route = (e.origin_iata && e.destination_iata)
      ? esc(e.origin_iata) + ' → ' + esc(e.destination_iata)
      : (e.airline ? esc(e.airline) : '');
    const altFmt = e.altitude ? Number(e.altitude).toLocaleString() + ' ft' : '?';
    const distFmt = e.distance_km != null ? e.distance_km + ' km' : '?';
    const extra = e.squawk && e.type === 'EMERGENCY' ? '<br><small>Sq ' + esc(e.squawk) + '</small>' : (e.reason ? '' : '');
    return `<tr>
      <td class="photo-cell" data-hex="${esc(e.hex)}"><button onclick="loadPhoto(this)">photo</button></td>
      <td><span class="t-rel" title="${absTime(e.timestamp)}">${relTime(e.timestamp)}</span><span class="t-abs">${absTime(e.timestamp)}</span></td>
      <td><span class="badge b-${e.type}">${e.type}</span></td>
      <td><a href="https://globe.adsbexchange.com/?icao=${esc(e.hex)}" target="_blank">${esc(e.hex).toUpperCase()}</a></td>
      <td>${esc(e.callsign) || '?'}</td>
      <td>${esc(e.reg) || '?'}</td>
      <td>${esc(e.aircraft_type) || '?'}</td>
      <td class="route-cell">${route}</td>
      <td>${distFmt}</td>
      <td>${altFmt}</td>
      <td class="reason-cell">${esc(e.reason) || ''}${extra}</td>
    </tr>`;
  }).join('');
}

function loadPhoto(btn) {
  const td = btn.parentElement;
  const hex = td.dataset.hex;
  if (photoCache[hex] !== undefined) { applyPhoto(td, photoCache[hex]); return; }
  td.innerHTML = '<span class="ph-miss">…</span>';
  fetch('/api/photo/' + hex).then(r => r.json()).then(d => {
    photoCache[hex] = d.url;
    applyPhoto(td, d.url);
  }).catch(() => { td.innerHTML = '<span class="ph-miss">—</span>'; });
}

function applyPhoto(td, url) {
  if (url) {
    td.innerHTML = `<a href="https://www.planespotters.net/hex/${td.dataset.hex.toUpperCase()}" target="_blank">` +
                   `<img src="${url}" alt="photo" title="Click to open Planespotters"></a>`;
  } else {
    td.innerHTML = '<span class="ph-miss">—</span>';
  }
}

function renderStats() {
  const c = {NOTIFY:0, SKIP:0, EXCLUDE:0, EMERGENCY:0};
  DATA.forEach(e => { if (c[e.type] !== undefined) c[e.type]++; });
  document.getElementById('stats').innerHTML =
    `<div class="stat notify"><div class="val">${c.NOTIFY}</div><div class="lbl">Notify</div></div>` +
    `<div class="stat skip"><div class="val">${c.SKIP}</div><div class="lbl">Skip</div></div>` +
    `<div class="stat exclude"><div class="val">${c.EXCLUDE}</div><div class="lbl">Exclude</div></div>` +
    `<div class="stat emergency"><div class="val">${c.EMERGENCY}</div><div class="lbl">Emergency</div></div>`;
}

document.querySelectorAll('.filter-bar input[type=checkbox]').forEach(el => el.addEventListener('change', render));
document.getElementById('search').addEventListener('input', render);

fetch('/api/notifications').then(r => r.json()).then(data => { DATA = data; renderStats(); render(); });

// ─── Config ──────────────────────────────────────────────────────────────────
const CFG_FIELDS = """ + json.dumps([
    {"key": k, "label": l, "type": t, "desc": d, "section": s}
    for k, l, t, d, s in CONFIG_FIELDS
]) + r""";

function buildForm(conf) {
  const sections = {};
  CFG_FIELDS.forEach(f => { (sections[f.section] = sections[f.section] || []).push(f); });
  const form = document.getElementById('cfg-form');
  form.innerHTML = Object.entries(sections).map(([sec, fields]) =>
    `<div class="cfg-section"><h2>${esc(sec)}</h2>` +
    fields.map(f => {
      const v = conf[f.key];
      if (f.type === 'checkbox') {
        const chk = (v === true || v === 'true') ? 'checked' : '';
        return `<div class="field">
          <div class="cb-row"><input type="checkbox" id="f-${f.key}" name="${f.key}" ${chk}><label for="f-${f.key}">${esc(f.label)}</label></div>
          <div class="desc">${esc(f.desc)}</div></div>`;
      }
      if (f.type === 'textarea') {
        const sv = v != null ? String(v) : '';
        return `<div class="field"><label for="f-${f.key}">${esc(f.label)}</label>
          <div class="desc">${esc(f.desc)}</div>
          <textarea id="f-${f.key}" name="${f.key}">${esc(sv)}</textarea></div>`;
      }
      const sv = (v != null) ? String(v) : '';
      return `<div class="field"><label for="f-${f.key}">${esc(f.label)}</label>
        <div class="desc">${esc(f.desc)}</div>
        <input type="${f.type}" id="f-${f.key}" name="${f.key}" value="${esc(sv)}"></div>`;
    }).join('') +
    '</div>'
  ).join('');
}

function saveConfig() {
  const updates = {};
  CFG_FIELDS.forEach(f => {
    const el = document.getElementById('f-' + f.key);
    if (!el) return;
    if (f.type === 'checkbox') updates[f.key] = el.checked;
    else if (f.type === 'number') updates[f.key] = el.value === '' ? null : Number(el.value);
    else updates[f.key] = el.value;
  });
  const msg = document.getElementById('save-msg');
  msg.textContent = 'Saving…'; msg.className = 'save-msg';
  fetch('/api/config', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify(updates)
  }).then(r => r.json()).then(d => {
    if (d.ok) { msg.textContent = '✓ Saved — changes apply on next poll'; msg.className = 'save-msg ok'; }
    else throw new Error();
    setTimeout(() => { msg.textContent = ''; }, 4000);
  }).catch(() => { msg.textContent = '✗ Save failed'; msg.className = 'save-msg err'; });
}

fetch('/api/config').then(r => r.json()).then(conf => buildForm(conf));
</script>
</body>
</html>
"""


class Handler(http.server.BaseHTTPRequestHandler):
    db_path = DB_PATH

    def do_GET(self):
        if self.path == "/api/notifications":
            self._json(_load_notifications(self.db_path))
        elif self.path == "/api/config":
            self._json(_load_config(self.db_path))
        elif self.path.startswith("/api/photo/"):
            hex_code = self.path.split("/")[-1]
            self._json({"url": _fetch_planespotters(hex_code)})
        else:
            self._html(HTML)

    def do_POST(self):
        if self.path == "/api/config":
            length = int(self.headers.get("Content-Length", 0))
            try:
                updates = json.loads(self.rfile.read(length))
            except (json.JSONDecodeError, ValueError):
                self.send_error(400, "Invalid JSON")
                return
            _save_config(self.db_path, updates)
            self._json({"ok": True})
        else:
            self.send_error(404)

    def _json(self, data):
        body = json.dumps(data).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", len(body))
        self.end_headers()
        self.wfile.write(body)

    def _html(self, content):
        body = content.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", len(body))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        pass  # suppress request logs


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="notify1090 web UI")
    parser.add_argument("--port", type=int, default=8888, help="port (default: 8888)")
    parser.add_argument("--db",   default=DB_PATH,        help="path to notify1090.db")
    args = parser.parse_args()
    Handler.db_path = args.db
    server = http.server.HTTPServer(("0.0.0.0", args.port), Handler)
    print(f"notify1090 web UI running on http://0.0.0.0:{args.port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
