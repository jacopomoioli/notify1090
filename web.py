#!/usr/bin/env python3
"""
notify1090 log viewer

Usage:
    python3 web.py [--port 8888] [--log log.txt]
"""
import argparse
import json
import re
import http.server
from pathlib import Path

import urllib.request
import urllib.error

PLANESPOTTERS_URL = "https://api.planespotters.net/pub/photos/hex/{hex}"
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"

LOG_PATTERN = re.compile(
    r"^(?P<timestamp>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\s+"
    r"(?:INFO|WARNING)\s+"
    r"(?P<type>NOTIFY|SKIP|EXCLUDE|EMERGENCY)\s+"
    r"(?P<hex>[0-9a-f]+)\s+"
    r"(?P<callsign>\S+)\s+"
    r"\((?P<reg>[^/]+)/(?P<aircraft_type>[^)]+)\)\s+"
    r"(?P<distance>[\d.]+)\s+km\s+"
    r"(?P<altitude>\S+)\s+ft"
    r"(?:\s+LLM REASON:\s*(?P<reason>.+))?$"
)


def parse_log(log_path):
    entries = []
    try:
        with open(log_path) as f:
            for line in f:
                m = LOG_PATTERN.match(line.strip())
                if m:
                    entries.append(m.groupdict())
    except FileNotFoundError:
        pass
    return entries


HTML = """\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>notify1090 log viewer</title>
<style>
* { box-sizing: border-box; margin: 0; padding: 0; }
body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, monospace; background: #0d1117; color: #c9d1d9; padding: 1.5rem; }
h1 { font-size: 1.3rem; margin-bottom: 1rem; color: #58a6ff; }
.filters { margin-bottom: 1rem; display: flex; gap: 1.2rem; align-items: center; flex-wrap: wrap; }
.filters label { cursor: pointer; display: flex; align-items: center; gap: 0.3rem; font-size: 0.85rem; }
.filters input[type="checkbox"] { accent-color: #58a6ff; }
.badge { display: inline-block; padding: 0.1rem 0.5rem; border-radius: 3px; font-size: 0.75rem; font-weight: 600; }
.badge-notify { background: #238636; color: #fff; }
.badge-skip { background: #da3633; color: #fff; }
.badge-exclude { background: #6e7681; color: #fff; }
.badge-emergency { background: #f0e040; color: #000; }
table { width: 100%; border-collapse: collapse; font-size: 0.82rem; }
thead { position: sticky; top: 0; background: #161b22; }
th, td { text-align: left; padding: 0.45rem 0.6rem; border-bottom: 1px solid #21262d; white-space: nowrap; }
th { color: #8b949e; font-weight: 500; text-transform: uppercase; font-size: 0.7rem; letter-spacing: 0.04em; }
tr:hover { background: #161b22; }
td.reason { white-space: normal; max-width: 400px; color: #8b949e; }
a { color: #58a6ff; text-decoration: none; }
a:hover { text-decoration: underline; }
.count { font-size: 0.8rem; color: #8b949e; margin-left: 1rem; }
td.photo { padding: 0.2rem 0.4rem; }
td.photo img { height: 72px; border-radius: 3px; cursor: pointer; }
td.photo button { background: none; border: 1px solid #30363d; color: #8b949e; border-radius: 3px; padding: 0.2rem 0.4rem; font-size: 0.7rem; cursor: pointer; }
td.photo button:hover { border-color: #58a6ff; color: #58a6ff; }
td.photo .loading { color: #30363d; font-size: 0.7rem; }
</style>
</head>
<body>
<h1>notify1090 log viewer</h1>
<div class="filters">
  <label><input type="checkbox" data-type="NOTIFY" checked> NOTIFY</label>
  <label><input type="checkbox" data-type="SKIP" checked> SKIP</label>
  <label><input type="checkbox" data-type="EXCLUDE"> EXCLUDE</label>
  <label><input type="checkbox" data-type="EMERGENCY" checked> EMERGENCY</label>
  <span class="count" id="count"></span>
</div>
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
  <th>Dist (km)</th>
  <th>Alt (ft)</th>
  <th>Reason</th>
</tr>
</thead>
<tbody id="tbody"></tbody>
</table>

<script>
let DATA = [];

const badgeClass = {
  NOTIFY: "badge-notify",
  SKIP: "badge-skip",
  EXCLUDE: "badge-exclude",
  EMERGENCY: "badge-emergency"
};

function render() {
  const checked = new Set(
    [...document.querySelectorAll('.filters input:checked')].map(el => el.dataset.type)
  );
  const filtered = DATA.filter(e => checked.has(e.type));
  document.getElementById("count").textContent = filtered.length + " / " + DATA.length + " entries";
  const tbody = document.getElementById("tbody");
  tbody.innerHTML = filtered.map(e => `<tr>
    <td class="photo" data-hex="${e.hex}"><button onclick="loadPhoto(this)">photo</button></td>
    <td>${e.timestamp}</td>
    <td><span class="badge ${badgeClass[e.type]}">${e.type}</span></td>
    <td><a href="https://globe.adsbexchange.com/?icao=${e.hex}" target="_blank">${e.hex.toUpperCase()}</a></td>
    <td>${e.callsign}</td>
    <td>${e.reg}</td>
    <td>${e.aircraft_type}</td>
    <td>${e.distance}</td>
    <td>${e.altitude}</td>
    <td class="reason">${e.reason || ""}</td>
  </tr>`).join("");
}

document.querySelectorAll('.filters input').forEach(el => el.addEventListener("change", render));

const photoCache = {};

function loadPhoto(btn) {
  const td = btn.parentElement;
  const hex = td.dataset.hex;
  if (photoCache[hex] !== undefined) {
    applyPhoto(td, photoCache[hex]);
    return;
  }
  td.innerHTML = '<span class="loading">...</span>';
  fetch("/api/photo/" + hex)
    .then(r => r.json())
    .then(d => {
      photoCache[hex] = d.url;
      applyPhoto(td, d.url);
    })
    .catch(() => { td.innerHTML = ""; });
}

function applyPhoto(td, url) {
  if (url) {
    td.innerHTML = `<a href="https://www.planespotters.net/hex/${td.dataset.hex.toUpperCase()}" target="_blank"><img src="${url}" alt="photo"></a>`;
  } else {
    td.innerHTML = "";
  }
}

fetch("/api/entries")
  .then(r => r.json())
  .then(data => { data.reverse(); DATA = data; render(); });
</script>
</body>
</html>
"""


def fetch_planespotters_thumb(hex_code):
    try:
        req = urllib.request.Request(PLANESPOTTERS_URL.format(hex=hex_code), headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode())
        photos = data.get("photos")
        if photos:
            return photos[0]["thumbnail_large"]["src"]
    except Exception:
        pass
    return None


class Handler(http.server.BaseHTTPRequestHandler):
    log_path = "log.txt"

    def do_GET(self):
        if self.path == "/api/entries":
            entries = parse_log(self.log_path)
            body = json.dumps(entries).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", len(body))
            self.end_headers()
            self.wfile.write(body)
        elif self.path.startswith("/api/photo/"):
            hex_code = self.path.split("/")[-1]
            url = fetch_planespotters_thumb(hex_code)
            body = json.dumps({"url": url}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", len(body))
            self.end_headers()
            self.wfile.write(body)
        else:
            body = HTML.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", len(body))
            self.end_headers()
            self.wfile.write(body)

    def log_message(self, fmt, *args):
        pass  # suppress request logs


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="notify1090 log viewer")
    parser.add_argument("--port", type=int, default=8888, help="port (default: 8888)")
    parser.add_argument("--log", default="log.txt", help="path to log file (default: log.txt)")
    args = parser.parse_args()
    Handler.log_path = args.log
    server = http.server.HTTPServer(("0.0.0.0", args.port), Handler)
    print(f"notify1090 log viewer running on http://0.0.0.0:{args.port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
