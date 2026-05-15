#!/usr/bin/env python3
import json
import logging
import math
import os
import re
import sqlite3
import sys
import tempfile
import time
import urllib.request
import urllib.error
import uuid

import io

try:
    from playwright.sync_api import sync_playwright
    HAS_PLAYWRIGHT = True
except ImportError:
    HAS_PLAYWRIGHT = False

try:
    from PIL import Image
    HAS_PILLOW = True
except ImportError:
    HAS_PILLOW = False

_fmt = logging.Formatter("%(asctime)s  %(levelname)-7s  %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
_console = logging.StreamHandler()
_console.setFormatter(_fmt)
_file = logging.FileHandler("log.txt")
_file.setFormatter(_fmt)
log = logging.getLogger("notify1090")
log.setLevel(logging.INFO)
log.addHandler(_console)
log.addHandler(_file)

DB_PATH = "notify1090.db"
TELEGRAM_URL = "https://api.telegram.org/bot{token}/sendMessage"
TELEGRAM_PHOTO_URL = "https://api.telegram.org/bot{token}/sendPhoto"
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
ADSBDB_URL = "https://api.adsbdb.com/v0/callsign/{callsign}"
PLANESPOTTERS_URL = "https://api.planespotters.net/pub/photos/hex/{hex}"
ADSBEXCHANGE_URL = "https://globe.adsbexchange.com/?icao={hex}"
NTFY_URL = "https://ntfy.sh/{topic}"
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36"

REGISTRATION_PREFIXES = [
    ("A6-", "🇦🇪"), ("A7-", "🇶🇦"), ("A9C", "🇧🇭"),
    ("AP-", "🇵🇰"), ("B-",  "🇨🇳"), ("C-",  "🇨🇦"),
    ("CC-", "🇨🇱"), ("CN-", "🇲🇦"), ("CS-", "🇵🇹"),
    ("CU-", "🇨🇺"), ("CX-", "🇺🇾"), ("D-",  "🇩🇪"),
    ("EC-", "🇪🇸"), ("EI-", "🇮🇪"), ("EK-", "🇦🇲"),
    ("EP-", "🇮🇷"), ("ER-", "🇲🇩"), ("ES-", "🇪🇪"),
    ("ET-", "🇪🇹"), ("EW-", "🇧🇾"), ("EX-", "🇰🇬"),
    ("EY-", "🇹🇯"), ("F-",  "🇫🇷"), ("G-",  "🇬🇧"),
    ("HA-", "🇭🇺"), ("HB-", "🇨🇭"), ("HC-", "🇪🇨"),
    ("HH-", "🇭🇹"), ("HI-", "🇩🇴"), ("HK-", "🇨🇴"),
    ("HL-", "🇰🇷"), ("HP-", "🇵🇦"), ("HR-", "🇭🇳"),
    ("HS-", "🇹🇭"), ("HZ-", "🇸🇦"), ("I-",  "🇮🇹"),
    ("JA-", "🇯🇵"), ("JU-", "🇲🇳"), ("JY-", "🇯🇴"),
    ("LN-", "🇳🇴"), ("LV-", "🇦🇷"), ("LX-", "🇱🇺"),
    ("LY-", "🇱🇹"), ("LZ-", "🇧🇬"), ("N",   "🇺🇸"),
    ("OB-", "🇵🇪"), ("OD-", "🇱🇧"), ("OE-", "🇦🇹"),
    ("OH-", "🇫🇮"), ("OK-", "🇨🇿"), ("OM-", "🇸🇰"),
    ("OO-", "🇧🇪"), ("OY-", "🇩🇰"), ("P4-", "🇦🇼"),
    ("PH-", "🇳🇱"), ("PJ-", "🇸🇽"), ("PP-", "🇧🇷"),
    ("PR-", "🇧🇷"), ("PT-", "🇧🇷"), ("PZ-", "🇸🇷"),
    ("RA-", "🇷🇺"), ("RF-", "🇷🇺"), ("RP-", "🇵🇭"),
    ("S2-", "🇧🇩"), ("S5-", "🇸🇮"), ("S7-", "🇸🇨"),
    ("SE-", "🇸🇪"), ("SP-", "🇵🇱"), ("ST-", "🇸🇩"),
    ("SU-", "🇪🇬"), ("SX-", "🇬🇷"), ("T7-", "🇸🇲"),
    ("TC-", "🇹🇷"), ("TF-", "🇮🇸"), ("TG-", "🇬🇹"),
    ("TI-", "🇨🇷"), ("TJ-", "🇨🇲"), ("TN-", "🇨🇬"),
    ("TS-", "🇹🇳"), ("TU-", "🇨🇮"), ("TY-", "🇧🇯"),
    ("TZ-", "🇲🇱"), ("UK-", "🇺🇿"), ("UN-", "🇰🇿"),
    ("UR-", "🇺🇦"), ("V2-", "🇦🇬"), ("V5-", "🇳🇦"),
    ("VH-", "🇦🇺"), ("VN-", "🇻🇳"), ("VP-B","🇧🇲"),
    ("VT-", "🇮🇳"), ("XA-", "🇲🇽"), ("XB-", "🇲🇽"),
    ("XC-", "🇲🇽"), ("YI-", "🇮🇶"), ("YJ-", "🇻🇺"),
    ("YK-", "🇸🇾"), ("YL-", "🇱🇻"), ("YR-", "🇷🇴"),
    ("YU-", "🇷🇸"), ("YV-", "🇻🇪"), ("Z-",  "🇿🇼"),
    ("ZA-", "🇦🇱"), ("ZK-", "🇳🇿"), ("ZS-", "🇿🇦"),
]

EMERGENCY_SQUAWKS = {
    "7500": ("HIJACK",        "☠️ HIJACK"),
    "7600": ("RADIO FAILURE", "📻 RADIO FAILURE"),
    "7700": ("EMERGENCY",     "🚨 EMERGENCY"),
}

def registration_nationality(reg):
    for prefix, country in REGISTRATION_PREFIXES:
        if reg.upper().startswith(prefix):
            return country
    return None

def haversine_km(lat1, lon1, lat2, lon2):
    R = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat / 2) ** 2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2) ** 2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))

def http_get_json(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read().decode())

def http_post_json(url, payload, headers=None, timeout=15):
    data = json.dumps(payload).encode()
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("User-Agent", UA)
    if headers:
        for k, v in headers.items():
            req.add_header(k, v)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode())


def db_init(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS seen_aircraft (
            hex TEXT PRIMARY KEY,
            first_seen INTEGER NOT NULL
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS notifications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp INTEGER NOT NULL,
            type TEXT NOT NULL,
            hex TEXT NOT NULL,
            callsign TEXT,
            reg TEXT,
            aircraft_type TEXT,
            distance_km REAL,
            altitude TEXT,
            reason TEXT,
            squawk TEXT,
            origin_iata TEXT,
            destination_iata TEXT,
            airline TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS config (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
    """)
    conn.commit()

def db_is_known(conn, hex_code, ttl_seconds):
    cutoff = int(time.time()) - ttl_seconds
    row = conn.execute(
        "SELECT 1 FROM seen_aircraft WHERE hex = ? AND first_seen > ?",
        (hex_code, cutoff)
    ).fetchone()
    return row is not None

def db_mark_seen(conn, hex_code):
    conn.execute(
        "INSERT OR REPLACE INTO seen_aircraft (hex, first_seen) VALUES (?, ?)",
        (hex_code, int(time.time()))
    )
    conn.commit()

def db_load_config(conn):
    rows = conn.execute("SELECT key, value FROM config").fetchall()
    conf = {}
    for key, value in rows:
        try:
            conf[key] = json.loads(value)
        except (json.JSONDecodeError, ValueError):
            conf[key] = value
    return conf

def db_save_config(conn, conf):
    for key, value in conf.items():
        conn.execute(
            "INSERT OR REPLACE INTO config (key, value) VALUES (?, ?)",
            (key, json.dumps(value))
        )
    conn.commit()

def db_log_notification(conn, notif_type, ac, reason=None, route=None):
    origin_iata = dest_iata = airline = None
    if route:
        orig = route.get("origin") or {}
        dest = route.get("destination") or {}
        origin_iata = orig.get("iata_code")
        dest_iata = dest.get("iata_code")
        airline = (route.get("airline") or {}).get("name")
    alt = ac.get("alt_baro", ac.get("altitude", ""))
    conn.execute("""
        INSERT INTO notifications
            (timestamp, type, hex, callsign, reg, aircraft_type,
             distance_km, altitude, reason, squawk,
             origin_iata, destination_iata, airline)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        int(time.time()),
        notif_type,
        ac.get("hex", ""),
        (ac.get("flight", "") or "").strip() or None,
        ac.get("r") or None,
        ac.get("t") or None,
        ac.get("_distance_km"),
        str(alt) if alt != "" else None,
        reason or None,
        ac.get("squawk") or None,
        origin_iata, dest_iata, airline,
    ))
    conn.commit()


def fetch_aircraft(tar1090_url):
    data = http_get_json(tar1090_url + "/data/aircraft.json")
    return data.get("aircraft", [])

def filter_nearby(aircraft_list, lat, lon, radius_km):
    nearby = []
    for ac in aircraft_list:
        ac_lat = ac.get("lat")
        ac_lon = ac.get("lon")
        if ac_lat is None or ac_lon is None:
            continue
        dist = haversine_km(lat, lon, ac_lat, ac_lon)
        if dist <= radius_km:
            ac["_distance_km"] = round(dist, 1)
            nearby.append(ac)
    return nearby

def format_aircraft_text(ac):
    fields = [
        ("Callsign", ac.get("flight", "").strip() or "unknown"),
        ("Registration", ac.get("r", "unknown")),
        ("Type", ac.get("desc", "unknown")),
        ("Squawk", ac.get("squawk", "unknown")),
    ]
    return "\n".join(f"{k}: {v}" for k, v in fields)


def ask_openrouter(api_key, model, user_prompt, aircraft_text):
    full_prompt = (
        user_prompt
        + "\n\nReply with YES or NO followed by a colon and a one-line reason. "
        "Example: YES: military tanker. or NO: common narrowbody.\n\nAircraft data:\n"
        + aircraft_text
    )
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": full_prompt}],
        "max_tokens": 40,
        "temperature": 0,
    }
    for attempt in range(3):
        try:
            resp = http_post_json(
                OPENROUTER_URL,
                payload,
                headers={"Authorization": f"Bearer {api_key}"},
                timeout=30,
            )
            raw = resp["choices"][0]["message"]["content"].strip()
            interesting = raw.upper().startswith("YES")
            reason = raw[raw.find(":")+1:].strip() if ":" in raw else ""
            return interesting, reason
        except Exception as e:
            if attempt < 2:
                time.sleep(2 ** attempt)
                continue
            raise


def send_telegram(bot_token, chat_id, text):
    url = TELEGRAM_URL.format(token=bot_token)
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML"
    }
    http_post_json(url, payload)

def send_ntfy(topic, title, body):
    url = NTFY_URL.format(topic=topic)
    data = body.encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Content-Type", "text/plain; charset=utf-8")
    req.add_header("Title", title.encode("utf-8").decode("latin-1", errors="replace"))
    req.add_header("Markdown", "yes")
    req.add_header("User-Agent", UA)
    with urllib.request.urlopen(req, timeout=15) as resp:
        resp.read()

def format_ntfy_message(ac, route=None, emergency=None, eval_failed=False, planespotters_url=None, tar1090_url=None):
    callsign = ac.get("flight", "").strip() or "unknown"
    reg = ac.get("r", "unknown")
    alt = ac.get("alt_baro", ac.get("altitude", "?"))
    speed = ac.get("gs", "?")
    dist = ac.get("_distance_km", "?")
    squawk = ac.get("squawk", "")
    airline = route.get("airline", {}).get("name", "") if route else ""
    hex_code = ac.get("hex", "")

    lines = []
    if emergency:
        lines.append(f"## {emergency}")
    if eval_failed:
        lines.append("⚠️ **Custom Evaluation Failed**")
    nationality = registration_nationality(reg)
    lines.append(f"Callsign: `{callsign}` · Reg: `{reg}`" + (f" · {nationality}" if nationality else ""))
    if route:
        orig = route.get("origin", {})
        dest = route.get("destination", {})
        if orig and dest:
            lines.append(f"{orig.get('iata_code','?')} {orig.get('municipality','?')} -> {dest.get('iata_code','?')} {dest.get('municipality','?')}")
    lines += [
        f"Alt: {round(alt * 0.3048)} m ({alt} ft)" if isinstance(alt, (int, float)) else "Alt: ?",
        f"Speed: {round(speed * 1.852)} km/h ({speed} kt)" if isinstance(speed, (int, float)) else "Speed: ?",
        f"Distance: {dist} km",
    ]
    if squawk:
        lines.append(f"Squawk: {squawk}")
    if planespotters_url:
        lines.append(f"![photo]({planespotters_url})")
    link_parts = []
    if tar1090_url:
        link_parts.append(f"[tar1090]({tar1090_url.rstrip('/')}/?icao={hex_code})")
    link_parts.append(f"[ADSBExchange]({ADSBEXCHANGE_URL.format(hex=hex_code)})")
    lines.append("  ".join(link_parts))
    return "\n".join(lines)

def ntfy_title(ac, route=None, emergency=None):
    type_code = ac.get("t", "?")
    desc = ac.get("desc", "")
    airline = route.get("airline", {}).get("name", "") if route else ""
    if emergency:
        _, tg_label = emergency if isinstance(emergency, tuple) else ("", emergency)
        return f"{tg_label} - {desc or type_code}"
    return f"{airline + ' - ' if airline else ''}{desc or type_code} ({type_code})"

def fetch_flightroute(callsign):
    try:
        data = http_get_json(ADSBDB_URL.format(callsign=callsign))
        route = data.get("response", {}).get("flightroute")
        if route:
            return route
    except Exception as e:
        log.warning("ADSBDB ERROR  %s - %s", callsign, e)
    return None

def fetch_planespotters_url(hex_code):
    try:
        data = http_get_json(PLANESPOTTERS_URL.format(hex=hex_code))
        photos = data.get("photos")
        if photos:
            return photos[0]["thumbnail_large"]["src"]
        log.info("PLANESPOTTERS  no photos for %s", hex_code)
    except Exception as e:
        log.warning("PLANESPOTTERS ERROR  %s - %s", hex_code, e)
    return None

def take_tar1090_screenshot(tar1090_url, hex_code, params="zoom=10&iconScale=1.0&hideSideBar&hideButtons&altitudeChart=0&screenshot", viewport=640):
    if not HAS_PLAYWRIGHT:
        return None
    vw = viewport
    vh = vw * 9 // 16
    url = f"{tar1090_url.rstrip('/')}/?icao={hex_code}&{params}"
    tmp = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
    tmp.close()
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page(viewport={"width": vw, "height": vh})
            page.goto(url, wait_until="networkidle", timeout=15000)
            page.wait_for_timeout(3000)
            page.evaluate("""
                document.querySelectorAll('#selected_infoblock, #infoblock, #infoblockLeft')
                    .forEach(el => el.style.display = 'none');
            """)
            page.locator("#map_canvas, #map").first.screenshot(path=tmp.name)
            browser.close()
        return tmp.name
    except Exception as e:
        log.warning("SCREENSHOT ERROR  %s - %s", hex_code, e)
        os.unlink(tmp.name)
        return None

def compose_images(planespotters_url, screenshot_path):
    """Fetch planespotter photo and compose it above the tar1090 screenshot into one image."""
    if not HAS_PILLOW:
        return None
    try:
        req = urllib.request.Request(planespotters_url, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=10) as resp:
            ps_img = Image.open(io.BytesIO(resp.read())).convert("RGB")
        sc_img = Image.open(screenshot_path).convert("RGB")
        w = max(ps_img.width, sc_img.width)
        if ps_img.width != w:
            ps_img = ps_img.resize((w, int(ps_img.height * w / ps_img.width)), Image.LANCZOS)
        if sc_img.width != w:
            sc_img = sc_img.resize((w, int(sc_img.height * w / sc_img.width)), Image.LANCZOS)
        composed = Image.new("RGB", (w, ps_img.height + sc_img.height))
        composed.paste(ps_img, (0, 0))
        composed.paste(sc_img, (0, ps_img.height))
        tmp = tempfile.NamedTemporaryFile(suffix=".jpg", delete=False)
        tmp.close()
        composed.save(tmp.name, "JPEG", quality=85)
        return tmp.name
    except Exception as e:
        log.warning("COMPOSE ERROR  %s", e)
        return None

def send_telegram_photo(bot_token, chat_id, photo_path, caption=None):
    url = TELEGRAM_PHOTO_URL.format(token=bot_token)
    boundary = uuid.uuid4().hex
    with open(photo_path, "rb") as f:
        photo_data = f.read()
    body = f"--{boundary}\r\nContent-Disposition: form-data; name=\"chat_id\"\r\n\r\n{chat_id}\r\n".encode()
    if caption:
        body += f"--{boundary}\r\nContent-Disposition: form-data; name=\"caption\"\r\n\r\n{caption}\r\n".encode()
        body += f"--{boundary}\r\nContent-Disposition: form-data; name=\"parse_mode\"\r\n\r\nHTML\r\n".encode()
    body += (
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"photo\"; filename=\"photo.jpg\"\r\nContent-Type: image/jpeg\r\n\r\n".encode()
        + photo_data
        + f"\r\n--{boundary}--\r\n".encode()
    )
    req = urllib.request.Request(url, data=body, method="POST")
    req.add_header("Content-Type", f"multipart/form-data; boundary={boundary}")
    req.add_header("User-Agent", UA)
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode())

def send_ntfy_with_image(topic, title, message, image_path):
    """Send a single ntfy notification with text (Message header) and image (binary body)."""
    url = NTFY_URL.format(topic=topic)
    with open(image_path, "rb") as f:
        data = f.read()
    # HTTP headers cannot contain real newlines; ntfy interprets literal \n as line breaks
    msg_header = "\\n".join(line for line in message.splitlines())
    msg_header = msg_header.encode("ascii", errors="ignore").decode("ascii")
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Content-Type", "image/jpeg")
    req.add_header("Filename", "photo.jpg")
    req.add_header("Title", title.encode("utf-8").decode("latin-1", errors="replace"))
    req.add_header("Message", msg_header)
    req.add_header("Markdown", "yes")
    req.add_header("User-Agent", UA)
    with urllib.request.urlopen(req, timeout=30) as resp:
        resp.read()

def format_telegram_message(ac, planespotters_url=None, eval_failed=False, route=None, emergency=None, embed_photo_link=True, tar1090_url=None):
    callsign = ac.get("flight", "").strip() or "unknown"
    reg = ac.get("r", "unknown")
    type_code = ac.get("t", "?")
    desc = ac.get("desc", "")
    alt = ac.get("alt_baro", ac.get("altitude", "?"))
    speed = ac.get("gs", "?")
    dist = ac.get("_distance_km", "?")
    squawk = ac.get("squawk", "")
    hex_code = ac.get("hex", "")

    airline = route.get("airline", {}).get("name", "") if route else ""
    first_line = f"<b>{airline + ' - ' if airline else ''}{desc or type_code} ({type_code})</b>"

    lines = []
    if emergency:
        lines.append(f"<b>{emergency}</b>")
    if eval_failed:
        lines.append("⚠️ Custom Evaluation Failed")
    nationality = registration_nationality(reg)
    lines += [
        first_line,
        f"Callsign: <code>{callsign}</code>  Reg: <code>{reg}</code>" + (f"  {nationality}" if nationality else ""),
    ]
    if route:
        orig = route.get("origin", {})
        dest = route.get("destination", {})
        if orig and dest:
            lines.append(f"Route: {orig.get('iata_code','?')} {orig.get('municipality','?')} -> {dest.get('iata_code','?')} {dest.get('municipality','?')}")
    lines += [
        f"Alt: {f'{round(alt * 0.3048)} m ({alt} ft)' if isinstance(alt, (int, float)) else '?'}",
        f"Speed: {f'{round(speed * 1.852)} km/h ({speed} kt)' if isinstance(speed, (int, float)) else '?'}",
        f"Distance: {dist} km",
    ]
    if squawk:
        lines.append(f"Squawk: {squawk}")

    # hidden link trick for planespotter preview - skip when photo is sent directly as attachment
    if planespotters_url and embed_photo_link:
        lines.append(f'<a href="{planespotters_url}">&#8203;</a>')

    link_parts = []
    if tar1090_url:
        link_parts.append(f'<a href="{tar1090_url.rstrip("/")}/?icao={hex_code}">tar1090</a>')
    link_parts.append(f'<a href="{ADSBEXCHANGE_URL.format(hex=hex_code)}">ADSBExchange</a>')
    lines.append("  ".join(link_parts))
    return "\n".join(lines)


def load_conf(path):
    with open(path) as f:
        return json.load(f)

def run(conf_path="conf.json", skip_llm=False, notify_all=False):
    conn = sqlite3.connect(DB_PATH)
    db_init(conn)

    # Bootstrap config from conf.json into DB if the table is empty
    if conn.execute("SELECT COUNT(*) FROM config").fetchone()[0] == 0:
        if os.path.exists(conf_path):
            db_save_config(conn, load_conf(conf_path))
            log.info("bootstrapped config from %s into database", conf_path)
        else:
            log.error("config table is empty and %s not found — cannot start", conf_path)
            sys.exit(1)

    if not HAS_PLAYWRIGHT:
        log.warning("playwright not installed - tar1090 screenshots disabled. Run: pip install playwright && playwright install chromium")
    if not HAS_PILLOW:
        log.warning("pillow not installed - composed image disabled. Run: pip install pillow")

    mode_tag = "  [notify-all]" if notify_all else ("  [skip-llm]" if skip_llm else "")

    poll_count = 0
    fail_streak = 0
    while True:
        poll_count += 1

        # Reload config from DB every poll so web UI changes take effect immediately
        conf = db_load_config(conn)

        required = ["tar1090_url", "latitude", "longitude", "radius_km", "poll_interval_seconds"]
        missing = [k for k in required if k not in conf]
        if missing:
            log.error("missing required config keys: %s — fix in web UI Settings", missing)
            time.sleep(10)
            continue

        use_telegram = bool(conf.get("telegram_bot_token") and conf.get("telegram_chat_id"))
        use_ntfy = bool(conf.get("ntfy_topic"))
        if not use_telegram and not use_ntfy:
            log.warning("poll #%d — no notification channel configured (set telegram or ntfy in Settings)", poll_count)
            time.sleep(int(conf.get("poll_interval_seconds") or 60))
            continue

        use_screenshot = bool(conf.get("screenshot", True))
        sc_params   = conf.get("screenshot_params", "zoom=10&iconScale=1.0&hideSideBar&hideButtons&altitudeChart=0&screenshot")
        sc_viewport = int(conf.get("screenshot_viewport") or 640)
        ttl_seconds = int(float(conf.get("seen_ttl_hours") or 1) * 3600)

        exclude_pattern = None
        raw_regex = conf.get("exclude_type_regex", "")
        if raw_regex:
            try:
                exclude_pattern = re.compile(raw_regex, re.IGNORECASE)
            except re.error as exc:
                log.warning("invalid exclude_type_regex '%s': %s", raw_regex, exc)

        if poll_count == 1:
            channels = " + ".join(filter(None, ["telegram" if use_telegram else None, "ntfy" if use_ntfy else None]))
            log.info("started - tar1090=%s  radius=%d km  interval=%ds  channels=%s%s",
                     conf["tar1090_url"], conf["radius_km"], conf["poll_interval_seconds"],
                     channels, mode_tag)
            log.info("TTL: aircraft re-evaluated after %dm", ttl_seconds // 60)

        try:
            aircraft_list = fetch_aircraft(conf["tar1090_url"])
            nearby = filter_nearby(aircraft_list, conf["latitude"], conf["longitude"], conf["radius_km"])
            new_count = sum(1 for ac in nearby if ac.get("hex") and not db_is_known(conn, ac["hex"], ttl_seconds))
            if fail_streak:
                log.info("poll #%d - recovered after %d failed poll(s)", poll_count, fail_streak)
                fail_streak = 0
            log.info("poll #%d — %d total / %d in radius / %d new",
                     poll_count, len(aircraft_list), len(nearby), new_count)

            for ac in nearby:
                hex_code = ac.get("hex")
                if not hex_code or db_is_known(conn, hex_code, ttl_seconds):
                    continue

                callsign = ac.get("flight", "").strip() or "?"
                reg = ac.get("r", "?")
                type_code = ac.get("t", "?")
                dist = ac.get("_distance_km", "?")
                alt = ac.get("alt_baro", ac.get("altitude", "?"))
                label = f"{hex_code} {callsign} ({reg}/{type_code}) {dist} km  {alt} ft"

                squawk = ac.get("squawk", "")
                emergency = EMERGENCY_SQUAWKS.get(squawk)
                if emergency:
                    log_label, tg_label = emergency
                    log.warning("EMERGENCY  %s  squawk=%s  %s", label, squawk, log_label)
                    db_mark_seen(conn, hex_code)
                    planespotters_url = fetch_planespotters_url(hex_code)
                    route = fetch_flightroute(callsign) if callsign != "?" else None
                    screenshot_path = take_tar1090_screenshot(conf["tar1090_url"], hex_code, sc_params, sc_viewport) if use_screenshot else None
                    composed_path = compose_images(planespotters_url, screenshot_path) if (planespotters_url and screenshot_path) else None
                    db_log_notification(conn, "EMERGENCY", ac, reason=log_label, route=route)
                    if use_telegram:
                        try:
                            if composed_path:
                                caption = format_telegram_message(ac, route=route, emergency=tg_label, embed_photo_link=False, tar1090_url=conf["tar1090_url"])
                                send_telegram_photo(conf["telegram_bot_token"], conf["telegram_chat_id"], composed_path, caption=caption)
                            elif planespotters_url:
                                msg = format_telegram_message(ac, planespotters_url, route=route, emergency=tg_label, tar1090_url=conf["tar1090_url"])
                                send_telegram(conf["telegram_bot_token"], conf["telegram_chat_id"], msg)
                            elif screenshot_path:
                                caption = format_telegram_message(ac, route=route, emergency=tg_label, embed_photo_link=False, tar1090_url=conf["tar1090_url"])
                                send_telegram_photo(conf["telegram_bot_token"], conf["telegram_chat_id"], screenshot_path, caption=caption)
                            else:
                                msg = format_telegram_message(ac, route=route, emergency=tg_label, tar1090_url=conf["tar1090_url"])
                                send_telegram(conf["telegram_bot_token"], conf["telegram_chat_id"], msg)
                        except Exception as e:
                            log.error("TELEGRAM ERROR  %s - %s", label, e)
                    if use_ntfy:
                        try:
                            image_for_ntfy = composed_path or screenshot_path
                            if image_for_ntfy:
                                send_ntfy_with_image(conf["ntfy_topic"], ntfy_title(ac, route, emergency), format_ntfy_message(ac, route=route, emergency=tg_label, tar1090_url=conf["tar1090_url"]), image_for_ntfy)
                            else:
                                send_ntfy(conf["ntfy_topic"], ntfy_title(ac, route, emergency), format_ntfy_message(ac, route=route, emergency=tg_label, planespotters_url=planespotters_url, tar1090_url=conf["tar1090_url"]))
                        except Exception as e:
                            log.error("NTFY ERROR  %s - %s", label, e)
                    if composed_path:
                        os.unlink(composed_path)
                    if screenshot_path:
                        os.unlink(screenshot_path)
                    continue

                if not notify_all and exclude_pattern and exclude_pattern.match(ac.get("t", "")):
                    log.info("EXCLUDE  %s", label)
                    db_mark_seen(conn, hex_code)
                    db_log_notification(conn, "EXCLUDE", ac)
                    continue

                db_mark_seen(conn, hex_code)
                ac_text = format_aircraft_text(ac)

                eval_failed = False
                reason = ""
                if notify_all or skip_llm:
                    interesting = True
                else:
                    try:
                        model = conf.get("openrouter_model") or "google/gemini-2.5-flash"
                        interesting, reason = ask_openrouter(conf["openrouter_api_key"], model, conf["prompt"], ac_text)
                    except Exception as e:
                        log.error("LLM ERROR  %s - %s  sending anyway", label, e)
                        interesting = True
                        eval_failed = True

                if interesting:
                    planespotters_url = fetch_planespotters_url(hex_code)
                    route = fetch_flightroute(callsign) if callsign != "?" else None
                    screenshot_path = take_tar1090_screenshot(conf["tar1090_url"], hex_code, sc_params, sc_viewport) if use_screenshot else None
                    composed_path = compose_images(planespotters_url, screenshot_path) if (planespotters_url and screenshot_path) else None
                    log.info("NOTIFY  %s%s", label, f"  LLM REASON: {reason}" if reason else "")
                    db_log_notification(conn, "NOTIFY", ac, reason=reason, route=route)
                    if use_telegram:
                        try:
                            if composed_path:
                                caption = format_telegram_message(ac, eval_failed=eval_failed, route=route, embed_photo_link=False, tar1090_url=conf["tar1090_url"])
                                send_telegram_photo(conf["telegram_bot_token"], conf["telegram_chat_id"], composed_path, caption=caption)
                            elif planespotters_url:
                                msg = format_telegram_message(ac, planespotters_url, eval_failed=eval_failed, route=route, tar1090_url=conf["tar1090_url"])
                                send_telegram(conf["telegram_bot_token"], conf["telegram_chat_id"], msg)
                            elif screenshot_path:
                                caption = format_telegram_message(ac, eval_failed=eval_failed, route=route, embed_photo_link=False, tar1090_url=conf["tar1090_url"])
                                send_telegram_photo(conf["telegram_bot_token"], conf["telegram_chat_id"], screenshot_path, caption=caption)
                            else:
                                msg = format_telegram_message(ac, eval_failed=eval_failed, route=route, tar1090_url=conf["tar1090_url"])
                                send_telegram(conf["telegram_bot_token"], conf["telegram_chat_id"], msg)
                        except Exception as e:
                            log.error("TELEGRAM ERROR  %s - %s", label, e)
                    if use_ntfy:
                        try:
                            image_for_ntfy = composed_path or screenshot_path
                            if image_for_ntfy:
                                send_ntfy_with_image(conf["ntfy_topic"], ntfy_title(ac, route), format_ntfy_message(ac, route=route, eval_failed=eval_failed, tar1090_url=conf["tar1090_url"]), image_for_ntfy)
                            else:
                                send_ntfy(conf["ntfy_topic"], ntfy_title(ac, route), format_ntfy_message(ac, route=route, eval_failed=eval_failed, planespotters_url=planespotters_url, tar1090_url=conf["tar1090_url"]))
                        except Exception as e:
                            log.error("NTFY ERROR  %s - %s", label, e)
                    if composed_path:
                        os.unlink(composed_path)
                    if screenshot_path:
                        os.unlink(screenshot_path)
                else:
                    log.info("SKIP  %s%s", label, f"  LLM REASON: {reason}" if reason else "")
                    db_log_notification(conn, "SKIP", ac, reason=reason)

        except urllib.error.URLError as e:
            fail_streak += 1
            reason = e.reason
            if isinstance(reason, OSError) and reason.errno == 111:
                kind = "connection refused"
            elif "timed out" in str(reason):
                kind = "timed out"
            else:
                kind = str(reason)
            log.warning("poll #%d - tar1090 unreachable (%s)  url=%s  streak=%d",
                        poll_count, kind, conf["tar1090_url"], fail_streak)
        except Exception as e:
            fail_streak += 1
            log.exception("poll #%d — unexpected error (streak=%d)", poll_count, fail_streak)

        time.sleep(int(conf.get("poll_interval_seconds") or 60))

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="notify1090 - ADS-B aircraft notification bot")
    parser.add_argument("conf", nargs="?", default="conf.json", help="path to conf.json")
    parser.add_argument("--skip-llm", action="store_true",
                        help="skip LLM evaluation but still apply the exclude_type_regex filter")
    parser.add_argument("--notify-all", action="store_true",
                        help="notify every never-seen-before aircraft in radius")
    parser.add_argument("--wipe-db", action="store_true",
                        help="delete all tracked aircraft from the database and exit")
    args = parser.parse_args()
    if args.wipe_db:
        conn = sqlite3.connect(DB_PATH)
        count = conn.execute("DELETE FROM seen_aircraft").rowcount
        conn.commit()
        conn.close()
        log.info("wiped %d aircraft from %s", count, DB_PATH)
        sys.exit(0)
    run(args.conf, skip_llm=args.skip_llm, notify_all=args.notify_all)
