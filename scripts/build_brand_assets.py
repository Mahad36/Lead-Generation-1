"""Build the audit PDF's brand assets from the Canva export (design/brand-template.pdf).

Run once after changing the Canva design:
    apps\\api\\.venv\\Scripts\\python.exe scripts\\build_brand_assets.py
Needs PyMuPDF (pip install pymupdf); the app itself only uses the files this writes to apps/api/app/assets/brand.

Writes:
  dark_bg.jpg            the dark page (gradient, hexagons, yellow curve, white logo) with its text removed
  page_*.jpg             the company pages used as they are (About, Shopify partner, clients, testimonials, thanks)
  logo_black.png         the logo for the white pages
  fonts/*.ttf            Bricolage Grotesque + Poppins (the Canva design's fonts)
"""

from __future__ import annotations

import io
import sys
import urllib.request
from pathlib import Path

import fitz  # PyMuPDF
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "design" / "brand-template.pdf"
OUT = ROOT / "apps" / "api" / "app" / "assets" / "brand"
DPI = 150  # A4 at 150 dpi = 1240 x 1754 px: sharp in a PDF viewer, small enough for an email attachment
JPEG_QUALITY = 84

# Company pages kept exactly as designed (0-based page index in the Canva export).
STATIC_PAGES = {"about": 1, "partner": 2, "clients": 11, "testimonials": 12, "thanks": 13}
DARK_TEMPLATE_PAGE = 13  # "Thank You": its background is reused (text removed) for the cover and offer pages
# (x0, y0, x1, y1) in pixels at 150 dpi: "Thank You", then the CONTACT pill with the email address
DARK_TEMPLATE_TEXT_AREAS = [(124, 636, 776, 994), (122, 1424, 540, 1562)]

FONTS = {
    "BricolageGrotesque-Regular.ttf": "bricolage-grotesque@5.2.5/latin-400-normal.ttf",
    "BricolageGrotesque-Bold.ttf": "bricolage-grotesque@5.2.5/latin-700-normal.ttf",
    "BricolageGrotesque-ExtraBold.ttf": "bricolage-grotesque@5.2.5/latin-800-normal.ttf",
    "Poppins-Bold.ttf": "poppins@5.2.5/latin-700-normal.ttf",
}


def render(page: fitz.Page) -> Image.Image:
    pix = page.get_pixmap(dpi=DPI)
    return Image.open(io.BytesIO(pix.tobytes("png"))).convert("RGB")


def erase(image: Image.Image, box: tuple[int, int, int, int], pad: int = 6) -> None:
    """Remove text on the smooth gradient: fill each row from the colours just left and right of the box."""
    x0, y0, x1, y1 = box
    x0, x1 = max(0, x0 - pad), min(image.width - 1, x1 + pad)
    y0, y1 = max(0, y0 - pad), min(image.height - 1, y1 + pad)
    px = image.load()
    left_x, right_x = max(0, x0 - 4), min(image.width - 1, x1 + 4)
    for y in range(y0, y1 + 1):
        left, right = px[left_x, y], px[right_x, y]
        span = max(1, right_x - left_x)
        for x in range(x0, x1 + 1):
            t = (x - left_x) / span
            px[x, y] = tuple(round(left[c] + (right[c] - left[c]) * t) for c in range(3))


def fill_smooth(image: Image.Image, boxes: list[tuple[int, int, int, int]], pad: int = 10) -> None:
    """Remove large text areas on a glowing gradient. Each area is filled with a surface that matches the pixels
    just outside all four of its edges and blends smoothly between them (a Coons patch), so no seams show."""
    from PIL import ImageFilter

    smooth = image.filter(ImageFilter.GaussianBlur(3)).load()  # edge samples without JPEG noise
    px = image.load()
    for bx0, by0, bx1, by1 in boxes:
        x0, y0 = max(1, bx0 - pad), max(1, by0 - pad)
        x1, y1 = min(image.width - 2, bx1 + pad), min(image.height - 2, by1 + pad)
        w, h = x1 - x0, y1 - y0
        top = [smooth[x, y0 - 1] for x in range(x0, x1 + 1)]
        bottom = [smooth[x, y1 + 1] for x in range(x0, x1 + 1)]
        left = [smooth[x0 - 1, y] for y in range(y0, y1 + 1)]
        right = [smooth[x1 + 1, y] for y in range(y0, y1 + 1)]
        tl, tr, bl, br = top[0], top[-1], bottom[0], bottom[-1]
        for j in range(h + 1):
            v = j / h
            for i in range(w + 1):
                u = i / w
                px[x0 + i, y0 + j] = tuple(
                    max(0, min(255, round(
                        (1 - v) * top[i][c] + v * bottom[i][c] + (1 - u) * left[j][c] + u * right[j][c]
                        - ((1 - u) * (1 - v) * tl[c] + u * (1 - v) * tr[c] + (1 - u) * v * bl[c] + u * v * br[c])
                    )))
                    for c in range(3)
                )


def to_px(rect: fitz.Rect) -> tuple[int, int, int, int]:
    scale = DPI / 72
    return (int(rect.x0 * scale), int(rect.y0 * scale), int(rect.x1 * scale) + 1, int(rect.y1 * scale) + 1)


def text_boxes(page: fitz.Page, words: list[str]) -> list[fitz.Rect]:
    boxes = []
    for word in words:
        boxes += page.search_for(word)
    return boxes


def main() -> None:
    if not SOURCE.exists():
        sys.exit(f"Missing {SOURCE}: export the Canva design as PDF and save it there.")
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "fonts").mkdir(exist_ok=True)
    doc = fitz.open(SOURCE)

    # Clean dark background: the "Thank You" page without its text (logo, hexagons and curve stay).
    # Canva flattened this page into one image, so its text is pixels: these are the areas at 150 dpi.
    page = doc[DARK_TEMPLATE_PAGE]
    image = render(page)
    fill_smooth(image, DARK_TEMPLATE_TEXT_AREAS)
    image.save(OUT / "dark_bg.jpg", quality=JPEG_QUALITY, optimize=True)
    print("dark_bg.jpg")

    # Company pages as designed, without the Canva page-number footers (the audit numbers its own pages).
    for name, index in STATIC_PAGES.items():
        page = doc[index]
        image = render(page)
        for rect in text_boxes(page, ["BEE CREATIVE | PAGE"]):
            erase(image, to_px(fitz.Rect(rect.x0, rect.y0, page.rect.width * 0.5, rect.y1)))
        image.save(OUT / f"page_{name}.jpg", quality=JPEG_QUALITY, optimize=True)
        print(f"page_{name}.jpg")

    # Black logo for the white pages (the image on the "Project Overview" page).
    page = doc[3]
    logo_rect = page.get_image_rects(page.get_images()[0][0])[0]
    pix = page.get_pixmap(dpi=300, clip=logo_rect)
    Image.open(io.BytesIO(pix.tobytes("png"))).save(OUT / "logo_black.png")
    print("logo_black.png")

    for filename, path in FONTS.items():
        with urllib.request.urlopen(f"https://cdn.jsdelivr.net/fontsource/fonts/{path}", timeout=30) as response:
            (OUT / "fonts" / filename).write_bytes(response.read())
        print(f"fonts/{filename}")


if __name__ == "__main__":
    main()
