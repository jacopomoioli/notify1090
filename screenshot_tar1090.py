#!/usr/bin/env python3
"""
Take a clean map screenshot of a specific aircraft from a tar1090 instance.

Usage:
    python3 screenshot_tar1090.py <icao> --url <tar1090_url> [options]

Deps:
    pip install playwright && playwright install chromium
"""
import argparse
from playwright.sync_api import sync_playwright

parser = argparse.ArgumentParser(
    description="Screenshot a specific aircraft from tar1090"
)
parser.add_argument("icao", help="ICAO hex code of the aircraft (e.g. 8964a4)")
parser.add_argument("--url", default="http://localhost:8080", help="base URL of your tar1090 instance (default: http://localhost:8080)")
parser.add_argument("--zoom", type=int, default=10, help="map zoom level 1-20 (default: 10)")
parser.add_argument("--icon-scale", type=float, default=1.0, help="aircraft icon scale multiplier (default: 1.0)")
parser.add_argument("--viewport", type=int, default=640, help="viewport width in px — smaller = bigger map labels (default: 640)")
parser.add_argument("--output", help="output file path (default: <icao>.png)")
args = parser.parse_args()

out = args.output or f"{args.icao}.png"
vw = args.viewport
vh = vw * 9 // 16  # 16:9 aspect ratio

url = (
    f"{args.url.rstrip('/')}/?icao={args.icao}"
    f"&zoom={args.zoom}"
    f"&iconScale={args.icon_scale}"
    f"&hideSideBar&hideButtons"
    f"&altitudeChart=0"
    f"&screenshot"
)

with sync_playwright() as p:
    browser = p.chromium.launch()
    page = browser.new_page(viewport={"width": vw, "height": vh})
    print(f"loading {url} ...")
    page.goto(url, wait_until="networkidle", timeout=15000)
    page.wait_for_timeout(3000)  # let map tiles and aircraft marker render

    # hideSideBar covers the right panel; hide the left aircraft info panel via JS
    page.evaluate("""
        document.querySelectorAll('#selected_infoblock, #infoblock, #infoblockLeft')
            .forEach(el => el.style.display = 'none');
    """)

    page.locator("#map_canvas, #map").first.screenshot(path=out)
    browser.close()

print(f"saved: {out}")
