#!/usr/bin/env python3
"""Capture screenshots and a short demo video of IL9Cast pages."""
import os
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE = os.environ.get("IL9_BASE", "http://127.0.0.1:8000")
OUT = Path(os.environ.get("IL9_ARTIFACTS", "/opt/cursor/artifacts"))
SHOTS = OUT / "screenshots"
VIDEOS = OUT / "videos"

PAGES = [
    ("home-desktop", "/", 1280, 900),
    ("home-mobile", "/", 390, 844),
    ("markets-desktop", "/markets", 1280, 900),
    ("markets-mobile", "/markets", 390, 844),
    ("model-desktop", "/odds", 1280, 1000),
    ("model-mobile", "/odds", 390, 844),
    ("methodology-desktop", "/methodology", 1280, 900),
    ("candidates-desktop", "/candidates", 1280, 900),
    ("about-desktop", "/about", 1280, 900),
]


def main():
    SHOTS.mkdir(parents=True, exist_ok=True)
    VIDEOS.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)

        for name, path, w, h in PAGES:
            ctx = browser.new_context(viewport={"width": w, "height": h})
            page = ctx.new_page()
            page.goto(f"{BASE}{path}", wait_until="networkidle", timeout=120000)
            page.wait_for_timeout(800)
            if "model" in name:
                try:
                    page.wait_for_selector("#precinct-map", timeout=60000)
                    page.wait_for_function(
                        "() => document.getElementById('map-loading').style.display === 'none'",
                        timeout=90000,
                    )
                    page.wait_for_timeout(1200)
                except Exception:
                    page.wait_for_timeout(3000)
            out = SHOTS / f"{name}.png"
            page.screenshot(path=str(out), full_page=True)
            print("screenshot", out)
            ctx.close()

        # Map interaction stills
        ctx = browser.new_context(viewport={"width": 1280, "height": 1000})
        page = ctx.new_page()
        page.goto(f"{BASE}/odds", wait_until="networkidle", timeout=120000)
        page.wait_for_selector("#precinct-map", timeout=60000)
        page.wait_for_function(
            "() => document.getElementById('map-loading').style.display === 'none'",
            timeout=90000,
        )
        page.wait_for_timeout(500)

        page.locator('.layer-pill[data-layer="competitive"]').click()
        page.wait_for_timeout(600)
        page.screenshot(path=str(SHOTS / "map-layer-competitive.png"), full_page=False)

        map_box = page.locator("#precinct-map").bounding_box()
        if map_box:
            page.mouse.click(map_box["x"] + map_box["width"] * 0.45, map_box["y"] + map_box["height"] * 0.42)
            page.wait_for_timeout(500)
        page.screenshot(path=str(SHOTS / "map-precinct-selected.png"), full_page=False)

        page.fill("#precinctMapSearch", "evan")
        page.wait_for_timeout(900)
        page.screenshot(path=str(SHOTS / "map-precinct-search.png"), full_page=False)
        ctx.close()

        # Demo video
        vid_ctx = browser.new_context(
            viewport={"width": 1280, "height": 800},
            record_video_dir=str(VIDEOS),
            record_video_size={"width": 1280, "height": 800},
        )
        vpage = vid_ctx.new_page()
        vpage.goto(f"{BASE}/odds", wait_until="networkidle", timeout=120000)
        vpage.wait_for_selector("#precinct-map", timeout=60000)
        vpage.wait_for_function(
            "() => document.getElementById('map-loading').style.display === 'none'",
            timeout=90000,
        )
        vpage.wait_for_timeout(1000)
        for layer in ["margin", "competitive", "biss", "winner"]:
            vpage.locator(f'.layer-pill[data-layer="{layer}"]').click()
            vpage.wait_for_timeout(700)
        mb = vpage.locator("#precinct-map").bounding_box()
        if mb:
            vpage.mouse.click(mb["x"] + mb["width"] * 0.5, mb["y"] + mb["height"] * 0.45)
            vpage.wait_for_timeout(800)
        vpage.fill("#precinctMapSearch", "skokie")
        vpage.wait_for_timeout(1000)
        vpage.locator("#mapResetView").click()
        vpage.wait_for_timeout(1200)
        vpage.goto(f"{BASE}/markets", wait_until="networkidle", timeout=120000)
        vpage.wait_for_timeout(1500)
        vpage.goto(f"{BASE}/", wait_until="networkidle", timeout=120000)
        vpage.wait_for_timeout(1500)

        video_path = vpage.video.path()
        vid_ctx.close()
        browser.close()

        if video_path:
            dest = VIDEOS / "ui-walkthrough.webm"
            Path(video_path).rename(dest)
            print("video", dest)

    print("done")


if __name__ == "__main__":
    main()
