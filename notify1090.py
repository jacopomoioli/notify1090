#!/usr/bin/env python3
import json
import logging
import math
import sqlite3
import sys
import time
import urllib.request
import urllib.error

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("notify1090")

DB_PATH = "notify1090.db"
TELEGRAM_URL = "https://api.telegram.org/bot{token}/sendMessage"
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash-lite:generateContent"
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

def http_post_json(url, payload, headers=None):
    data = json.dumps(payload).encode()
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("User-Agent", UA)
    if headers:
        for k, v in headers.items():
            req.add_header(k, v)
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read().decode())


def db_init(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS seen_aircraft (
            hex TEXT PRIMARY KEY,
            first_seen INTEGER NOT NULL
        )
    """)
    conn.commit()

def db_is_known(conn, hex_code):
    row = conn.execute("SELECT 1 FROM seen_aircraft WHERE hex = ?", (hex_code,)).fetchone()
    return row is not None

def db_mark_seen(conn, hex_code):
    conn.execute(
        "INSERT OR IGNORE INTO seen_aircraft (hex, first_seen) VALUES (?, ?)",
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
                "text": user_prompt + "\n\nAircraft data:\n" + aircraft_text
            }]
        }],
        "generationConfig": {
            "maxOutputTokens": 10,
            "temperature": 0
        }
    }
    resp = http_post_json(url, payload)
    answer = resp["candidates"][0]["content"]["parts"][0]["text"].strip().upper()
    return answer.startswith("YES")


def send_telegram(bot_token, chat_id, text):
    url = TELEGRAM_URL.format(token=bot_token)
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML"
    }
    http_post_json(url, payload)

def fetch_planespotters_url(hex_code):
    try:
        data = http_get_json(f"https://api.planespotters.net/pub/photos/hex/{hex_code}")
        photos = data.get("photos")
        if photos:
            return photos[0]["thumbnail_large"]["src"]
        log.info("planespotters — no photos for %s", hex_code)
    except Exception as e:
        log.warning("planespotters error for %s — %s", hex_code, e)
    return None

def format_telegram_message(ac, planespotters_url=None):
    callsign = ac.get("flight", "").strip() or "unknown"
    reg = ac.get("r", "unknown")
    type_code = ac.get("t", "?")
    desc = ac.get("desc", "")
    type_str = f"{type_code} — {desc}" if desc else type_code
    alt = ac.get("alt_baro", ac.get("altitude", "?"))
    speed = ac.get("gs", "?")
    dist = ac.get("_distance_km", "?")
    squawk = ac.get("squawk", "")

    lines = []
    lines += [
        f"Type: <code>{type_str}</code>",
        f"Callsign: <code>{callsign}</code>  Reg: <code>{reg}</code>",
        f"Alt: {f'{round(alt * 0.3048)} m ({alt} ft)' if isinstance(alt, (int, float)) else '?'}  ",
        f"Speed: {f'{round(speed * 1.852)} km/h ({speed} kt)' if isinstance(speed, (int, float)) else '?'}  ",
        f"Distance: {dist} km",
    ]
    if squawk:
        lines.append(f"Squawk: {squawk}")

    # planespotters url as a white character cuz we care about the picture only, not the url
    if planespotters_url:
        lines.append(f'<a href="{planespotters_url}">&#8203;</a>')

    lines.append(f"https://globe.adsbexchange.com/?icao={ac.get('hex', '')}")
    return "\n".join(lines)


def load_conf(path):
    with open(path) as f:
        return json.load(f)

def run(conf_path, notify_all=False):
    conf = load_conf(conf_path)
    conn = sqlite3.connect(DB_PATH)
    db_init(conn)

    log.info("started — tar1090=%s  radius=%d km  interval=%ds%s",
             conf["tar1090_url"], conf["radius_km"], conf["poll_interval_seconds"],
             "  [notify-all]" if notify_all else "")

    poll_count = 0
    fail_streak = 0
    while True:
        poll_count += 1
        try:
            aircraft_list = fetch_aircraft(conf["tar1090_url"])
            nearby = filter_nearby(aircraft_list, conf["latitude"], conf["longitude"], conf["radius_km"])
            new_count = sum(1 for ac in nearby if ac.get("hex") and not db_is_known(conn, ac["hex"]))
            if fail_streak:
                log.info("poll #%d — recovered after %d failed poll(s)", poll_count, fail_streak)
                fail_streak = 0
            log.info("poll #%d — %d total / %d in radius / %d new",
                     poll_count, len(aircraft_list), len(nearby), new_count)

            for ac in nearby:
                hex_code = ac.get("hex")
                if not hex_code or db_is_known(conn, hex_code):
                    continue

                callsign = ac.get("flight", "").strip() or "?"
                reg = ac.get("r", "?")
                type_code = ac.get("t", "?")
                dist = ac.get("_distance_km", "?")
                alt = ac.get("alt_baro", ac.get("altitude", "?"))
                label = f"{hex_code} {callsign} ({reg}/{type_code}) {dist} km  {alt} ft"

                db_mark_seen(conn, hex_code)
                ac_text = format_aircraft_text(ac)

                if notify_all:
                    interesting = True
                else:
                    try:
                        interesting = ask_gemini(conf["gemini_api_key"], conf["prompt"], ac_text)
                    except Exception as e:
                        log.error("gemini error  %s — %s", label, e)
                        continue

                if interesting:
                    planespotters_url = fetch_planespotters_url(hex_code)
                    msg = format_telegram_message(ac, planespotters_url)
                    try:
                        send_telegram(conf["telegram_bot_token"], conf["telegram_chat_id"], msg)
                        log.info("NOTIFY  %s", label)
                    except Exception as e:
                        log.error("telegram error  %s — %s", label, e)
                else:
                    log.info("skip    %s", label)

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
