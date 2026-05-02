#!/usr/bin/env python3
import json
import logging
import math
import re
import sqlite3
import sys
import time
import urllib.request
import urllib.error

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
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash-lite:generateContent"
ADSBDB_URL = "https://api.adsbdb.com/v0/callsign/{callsign}"
PLANESPOTTERS_URL = "https://api.planespotters.net/pub/photos/hex/{hex}"
ADSBEXCHANGE_URL = "https://globe.adsbexchange.com/?icao={hex}"
NTFY_URL = "https://ntfy.sh/{topic}"

EMERGENCY_SQUAWKS = {
    "7500": ("HIJACK",        "☠️ HIJACK"),
    "7600": ("RADIO FAILURE", "📻 RADIO FAILURE"),
    "7700": ("EMERGENCY",     "🚨 EMERGENCY"),
}
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"


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


def ask_gemini(api_key, user_prompt, aircraft_text):
    url = GEMINI_URL + "?key=" + api_key
    payload = {
        "contents": [{
            "parts": [{
                "text": user_prompt + "\n\nReply with YES or NO followed by a colon and a one-line reason. Example: YES: military tanker. or NO: common narrowbody.\n\nAircraft data:\n" + aircraft_text
            }]
        }],
        "generationConfig": {
            "maxOutputTokens": 40,
            "temperature": 0
        }
    }
    resp = http_post_json(url, payload, timeout=30)
    raw = resp["candidates"][0]["content"]["parts"][0]["text"].strip()
    upper = raw.upper()
    interesting = upper.startswith("YES")
    reason = raw[raw.find(":")+1:].strip() if ":" in raw else ""
    return interesting, reason


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

def format_ntfy_message(ac, route=None, emergency=None, eval_failed=False, planespotters_url=None):
    callsign = ac.get("flight", "").strip() or "unknown"
    reg = ac.get("r", "unknown")
    type_code = ac.get("t", "?")
    desc = ac.get("desc", "")
    alt = ac.get("alt_baro", ac.get("altitude", "?"))
    speed = ac.get("gs", "?")
    dist = ac.get("_distance_km", "?")
    squawk = ac.get("squawk", "")
    airline = route.get("airline", {}).get("name", "") if route else ""

    lines = []
    if emergency:
        lines.append(f"## {emergency}")
    if eval_failed:
        lines.append("⚠️ **Custom Evaluation Failed**")
    lines.append(f"Callsign: `{callsign}` · Reg: `{reg}`")
    if route:
        orig = route.get("origin", {})
        dest = route.get("destination", {})
        if orig and dest:
            lines.append(f"{orig.get('iata_code','?')} {orig.get('municipality','?')} → {dest.get('iata_code','?')} {dest.get('municipality','?')}")
    lines += [
        f"Alt: {round(alt * 0.3048)} m ({alt} ft)" if isinstance(alt, (int, float)) else "Alt: ?",
        f"Speed: {round(speed * 1.852)} km/h ({speed} kt)" if isinstance(speed, (int, float)) else "Speed: ?",
        f"Distance: {dist} km",
    ]
    if squawk:
        lines.append(f"Squawk: {squawk}")
    if planespotters_url:
        lines.append(f"![photo]({planespotters_url})")
    lines.append(f"[ADSBExchange]({ADSBEXCHANGE_URL.format(hex=ac.get('hex', ''))})")
    return "\n".join(lines)

def ntfy_title(ac, route=None, emergency=None):
    type_code = ac.get("t", "?")
    desc = ac.get("desc", "")
    airline = route.get("airline", {}).get("name", "") if route else ""
    if emergency:
        _, tg_label = emergency if isinstance(emergency, tuple) else ("", emergency)
        return f"{tg_label} — {desc or type_code}"
    return f"{airline + ' — ' if airline else ''}{desc or type_code} ({type_code})"

def fetch_flightroute(callsign):
    try:
        data = http_get_json(ADSBDB_URL.format(callsign=callsign))
        route = data.get("response", {}).get("flightroute")
        if route:
            return route
    except Exception as e:
        log.warning("ADSBDB ERROR  %s — %s", callsign, e)
    return None

def fetch_planespotters_url(hex_code):
    try:
        data = http_get_json(PLANESPOTTERS_URL.format(hex=hex_code))
        photos = data.get("photos")
        if photos:
            return photos[0]["thumbnail_large"]["src"]
        log.info("PLANESPOTTERS  no photos for %s", hex_code)
    except Exception as e:
        log.warning("PLANESPOTTERS ERROR  %s — %s", hex_code, e)
    return None

def format_telegram_message(ac, planespotters_url=None, eval_failed=False, route=None, emergency=None):
    callsign = ac.get("flight", "").strip() or "unknown"
    reg = ac.get("r", "unknown")
    type_code = ac.get("t", "?")
    desc = ac.get("desc", "")
    type_str = f"{type_code} — {desc}" if desc else type_code
    alt = ac.get("alt_baro", ac.get("altitude", "?"))
    speed = ac.get("gs", "?")
    dist = ac.get("_distance_km", "?")
    squawk = ac.get("squawk", "")

    airline = route.get("airline", {}).get("name", "") if route else ""
    first_line = f"<b>{airline + ' — ' if airline else ''}{desc or type_code} ({type_code})</b>"

    lines = []
    if emergency:
        lines.append(f"<b>{emergency}</b>")
    if eval_failed:
        lines.append("⚠️ Custom Evaluation Failed")
    lines += [
        first_line,
        f"Callsign: <code>{callsign}</code>  Reg: <code>{reg}</code>",
    ]
    if route:
        orig = route.get("origin", {})
        dest = route.get("destination", {})
        if orig and dest:
            lines.append(f"Route: {orig.get('iata_code','?')} {orig.get('municipality','?')} → {dest.get('iata_code','?')} {dest.get('municipality','?')}")
    lines += [
        f"Alt: {f'{round(alt * 0.3048)} m ({alt} ft)' if isinstance(alt, (int, float)) else '?'}",
        f"Speed: {f'{round(speed * 1.852)} km/h ({speed} kt)' if isinstance(speed, (int, float)) else '?'}",
        f"Distance: {dist} km",
    ]
    if squawk:
        lines.append(f"Squawk: {squawk}")

    # planespotters url as a white character cuz we care about the picture only, not the url
    if planespotters_url:
        lines.append(f'<a href="{planespotters_url}">&#8203;</a>')

    lines.append(ADSBEXCHANGE_URL.format(hex=ac.get("hex", "")))
    return "\n".join(lines)


def load_conf(path):
    with open(path) as f:
        return json.load(f)

def run(conf_path, notify_all=False):
    conf = load_conf(conf_path)

    use_telegram = bool(conf.get("telegram_bot_token") and conf.get("telegram_chat_id"))
    use_ntfy = bool(conf.get("ntfy_topic"))
    if not use_telegram and not use_ntfy:
        log.error("no notification channel configured — set telegram_bot_token/telegram_chat_id or ntfy_topic")
        sys.exit(1)
    channels = " + ".join(filter(None, ["telegram" if use_telegram else None, "ntfy" if use_ntfy else None]))

    conn = sqlite3.connect(DB_PATH)
    db_init(conn)

    log.info("started — tar1090=%s  radius=%d km  interval=%ds  channels=%s%s",
             conf["tar1090_url"], conf["radius_km"], conf["poll_interval_seconds"],
             channels, "  [notify-all]" if notify_all else "")

    ttl_seconds = int(conf.get("seen_ttl_hours", 1) * 3600)
    log.info("TTL: aircraft re-evaluated after %dm", ttl_seconds // 60)

    exclude_pattern = None
    if conf.get("exclude_type_regex"):
        exclude_pattern = re.compile(conf["exclude_type_regex"], re.IGNORECASE)
        log.info("exclude regex: %s", conf["exclude_type_regex"])

    poll_count = 0
    fail_streak = 0
    while True:
        poll_count += 1
        try:
            aircraft_list = fetch_aircraft(conf["tar1090_url"])
            nearby = filter_nearby(aircraft_list, conf["latitude"], conf["longitude"], conf["radius_km"])
            new_count = sum(1 for ac in nearby if ac.get("hex") and not db_is_known(conn, ac["hex"], ttl_seconds))
            if fail_streak:
                log.info("poll #%d — recovered after %d failed poll(s)", poll_count, fail_streak)
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
                    if use_telegram:
                        msg = format_telegram_message(ac, planespotters_url, route=route, emergency=tg_label)
                        try:
                            send_telegram(conf["telegram_bot_token"], conf["telegram_chat_id"], msg)
                        except Exception as e:
                            log.error("TELEGRAM ERROR  %s — %s", label, e)
                    if use_ntfy:
                        try:
                            send_ntfy(conf["ntfy_topic"], ntfy_title(ac, route, emergency), format_ntfy_message(ac, route=route, emergency=tg_label, planespotters_url=planespotters_url))
                        except Exception as e:
                            log.error("NTFY ERROR  %s — %s", label, e)
                    continue

                if exclude_pattern and exclude_pattern.match(ac.get("t", "")):
                    log.info("EXCLUDE  %s", label)
                    db_mark_seen(conn, hex_code)
                    continue

                db_mark_seen(conn, hex_code)
                ac_text = format_aircraft_text(ac)

                eval_failed = False
                reason = ""
                if notify_all:
                    interesting = True
                else:
                    try:
                        interesting, reason = ask_gemini(conf["gemini_api_key"], conf["prompt"], ac_text)
                    except Exception as e:
                        log.error("GEMINI ERROR  %s — %s  retrying...", label, e)
                        try:
                            interesting, reason = ask_gemini(conf["gemini_api_key"], conf["prompt"], ac_text)
                        except Exception as e2:
                            log.error("GEMINI RETRY FAILED  %s — %s  sending anyway", label, e2)
                            interesting = True
                            eval_failed = True

                if interesting:
                    planespotters_url = fetch_planespotters_url(hex_code)
                    route = fetch_flightroute(callsign) if callsign != "?" else None
                    log.info("NOTIFY  %s%s", label, f"  LLM REASON: {reason}" if reason else "")
                    if use_telegram:
                        msg = format_telegram_message(ac, planespotters_url, eval_failed=eval_failed, route=route)
                        try:
                            send_telegram(conf["telegram_bot_token"], conf["telegram_chat_id"], msg)
                        except Exception as e:
                            log.error("TELEGRAM ERROR  %s — %s", label, e)
                    if use_ntfy:
                        try:
                            send_ntfy(conf["ntfy_topic"], ntfy_title(ac, route), format_ntfy_message(ac, route=route, eval_failed=eval_failed, planespotters_url=planespotters_url))
                        except Exception as e:
                            log.error("NTFY ERROR  %s — %s", label, e)
                else:
                    log.info("SKIP  %s%s", label, f"  LLM REASON: {reason}" if reason else "")

        except urllib.error.URLError as e:
            fail_streak += 1
            reason = e.reason
            if isinstance(reason, OSError) and reason.errno == 111:
                kind = "connection refused"
            elif "timed out" in str(reason):
                kind = "timed out"
            else:
                kind = str(reason)
            log.warning("poll #%d — tar1090 unreachable (%s)  url=%s  streak=%d",
                        poll_count, kind, conf["tar1090_url"], fail_streak)
        except Exception as e:
            fail_streak += 1
            log.exception("poll #%d — unexpected error (streak=%d)", poll_count, fail_streak)

        time.sleep(conf["poll_interval_seconds"])

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="notify1090 — ADS-B aircraft notification bot")
    parser.add_argument("conf", nargs="?", default="conf.json", help="path to conf.json")
    parser.add_argument("--notify-all", action="store_true",
                        help="skip Gemini and send a Telegram alert for every new aircraft")
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
    run(args.conf, notify_all=args.notify_all)
