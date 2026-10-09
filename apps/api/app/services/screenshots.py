"""Screenshots of a website as a visitor sees it, for Claude's visual design / CRO review.

Uses Playwright's Chromium (installed with `playwright install chromium`). Captures the first few screens
on a desktop and a phone viewport, scrolling like a visitor so lazy-loaded content appears.
"""

from __future__ import annotations

from pathlib import Path

from app.logging_config import get_logger

logger = get_logger("screenshots")

DEVICES = [
    {"name": "desktop", "viewport": {"width": 1440, "height": 900}, "scale": 1, "mobile": False},
    {"name": "mobile", "viewport": {"width": 390, "height": 844}, "scale": 2, "mobile": True},
]
MOBILE_UA = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1"
)
SCREENS_PER_DEVICE = 3
SETTLE_MS = 1200
NAV_TIMEOUT_MS = 45_000


class ScreenshotError(RuntimeError):
    pass


def capture_screenshots(url: str, out_dir: str) -> dict:
    """Returns {"desktop": [png paths], "mobile": [png paths]} (first screen first)."""
    try:
        from playwright.sync_api import Error as PlaywrightError
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise ScreenshotError("Playwright is not installed (pip install playwright).") from exc

    folder = Path(out_dir)
    folder.mkdir(parents=True, exist_ok=True)
    result: dict[str, list[str]] = {}
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(args=["--no-sandbox", "--disable-dev-shm-usage"])
            try:
                for device in DEVICES:
                    context = browser.new_context(
                        viewport=device["viewport"],
                        device_scale_factor=device["scale"],
                        is_mobile=device["mobile"],
                        has_touch=device["mobile"],
                        user_agent=MOBILE_UA if device["mobile"] else None,
                        locale="en-US",
                    )
                    page = context.new_page()
                    try:
                        page.goto(url, wait_until="load", timeout=NAV_TIMEOUT_MS)
                    except PlaywrightError:
                        # Slow third-party assets: take what has rendered so far.
                        page.wait_for_timeout(SETTLE_MS)
                    page.wait_for_timeout(SETTLE_MS)
                    height = device["viewport"]["height"]
                    page_height = page.evaluate("document.documentElement.scrollHeight") or height
                    screens = max(1, min(SCREENS_PER_DEVICE, -(-int(page_height) // height)))
                    paths = []
                    last_y = -1
                    for index in range(screens):
                        page.evaluate("(y) => window.scrollTo(0, y)", index * height)
                        page.wait_for_timeout(SETTLE_MS)
                        y = page.evaluate("window.scrollY")
                        if index and y <= last_y:
                            break  # reached the bottom; no blank or repeated screens
                        last_y = y
                        path = folder / f"{device['name']}-{index + 1}.png"
                        page.screenshot(path=str(path))
                        paths.append(str(path))
                    result[device["name"]] = paths
                    context.close()
            finally:
                browser.close()
    except PlaywrightError as exc:
        message = str(exc).splitlines()[0][:220]
        if "Executable doesn't exist" in str(exc):
            message = "Chromium for Playwright is not installed. Run: playwright install --with-deps chromium"
        raise ScreenshotError(f"Screenshots failed: {message}") from exc
    logger.info("screenshots_captured", url=url, desktop=len(result.get("desktop", [])), mobile=len(result.get("mobile", [])))
    return result
