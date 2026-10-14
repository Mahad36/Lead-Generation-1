"""Render the client-facing website audit PDF in the Bee Creative brand (reportlab, no browser needed).

Look and company pages come from the Canva design (design/brand-template.pdf), turned into assets by
scripts/build_brand_assets.py: app/assets/brand/ holds the clean dark background (logo, hexagons, yellow curve), the
company pages used as designed (About, Shopify partner, clients, testimonials, thank you), the black logo and the
Bricolage Grotesque / Poppins fonts.

Page order: cover (dark) -> About Bee Creative -> the audit of this website (white pages: at a glance,
screenshots, findings, what works, action plan, under the hood) -> the free offer (dark) -> Shopify partner ->
clients -> testimonials -> thank you.
"""

from __future__ import annotations

import io
import re
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    BaseDocTemplate,
    CondPageBreak,
    Flowable,
    Frame,
    KeepTogether,
    NextPageTemplate,
    PageBreak,
    PageTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
)

from app.utils.normalize import extract_domain

BRAND_DIR = Path(__file__).resolve().parent.parent / "assets" / "brand"

YELLOW = colors.HexColor("#FEE12D")
INK = colors.HexColor("#111111")
BODY = colors.HexColor("#2B2B2B")
MUTED = colors.HexColor("#6B6B6B")
LINE = colors.HexColor("#E6E6E6")
TRACK = colors.HexColor("#F1F1F1")
PANEL = colors.HexColor("#FAFAF7")
GOOD = colors.HexColor("#15803D")
WARN = colors.HexColor("#B45309")
BAD = colors.HexColor("#C2410C")
SEVERITY = {
    "critical": ("Critical", colors.HexColor("#B91C1C")),
    "high": ("High", colors.HexColor("#C2410C")),
    "medium": ("Medium", colors.HexColor("#A16207")),
    "low": ("Low", colors.HexColor("#4B5563")),
}

PAGE_W, PAGE_H = A4
MARGIN = 20 * mm
CONTENT_W = PAGE_W - 2 * MARGIN
LOGO_TOP = 16 * mm  # white pages: centred logo, like the Canva content pages
BODY_TOP = 42 * mm

# Company pages from the Canva design, in the order they appear around the audit.
INTRO_PAGES = ["page_about.jpg"]
OUTRO_PAGES = ["page_partner.jpg", "page_clients.jpg", "page_testimonials.jpg", "page_thanks.jpg"]

_UNICODE_FALLBACKS = str.maketrans(
    {"≥": ">=", "≤": "<=", "→": "->", "←": "<-", "✓": "OK", "✔": "OK", "✗": "x", "×": "x", "−": "-", " ": " "}
)


# ---------------------------------------------------------------- fonts and text


@lru_cache(maxsize=1)
def _fonts() -> dict[str, str]:
    """Register the Canva fonts; fall back to Helvetica if the brand pack is missing."""
    files = {
        "body": "BricolageGrotesque-Regular.ttf",
        "bold": "BricolageGrotesque-Bold.ttf",
        "black": "BricolageGrotesque-ExtraBold.ttf",
        "label": "Poppins-Bold.ttf",
    }
    names = {"body": "Helvetica", "bold": "Helvetica-Bold", "black": "Helvetica-Bold", "label": "Helvetica-Bold"}
    for key, filename in files.items():
        path = BRAND_DIR / "fonts" / filename
        if path.exists():
            name = f"BC-{key}"
            if name not in pdfmetrics.getRegisteredFontNames():
                pdfmetrics.registerFont(TTFont(name, str(path)))
            names[key] = name
    return names


def _clean(value) -> str:
    """Plain text limited to what the fonts can draw (Latin-1)."""
    text = str(value if value is not None else "").translate(_UNICODE_FALLBACKS)
    return text.encode("latin-1", errors="replace").decode("latin-1")


def _safe(value) -> str:
    return escape(_clean(value))


def _plain(value: str) -> str:
    return _clean(value)


def score_color(score) -> colors.Color:
    if score is None:
        return MUTED
    if score >= 90:
        return GOOD
    if score >= 50:
        return WARN
    return BAD


def _styles() -> dict[str, ParagraphStyle]:
    f = _fonts()
    base = ParagraphStyle("base", fontName=f["body"], fontSize=10.5, leading=15.5, textColor=BODY)
    return {
        "body": base,
        "lead": ParagraphStyle("lead", parent=base, fontSize=11.5, leading=17, textColor=INK),
        "small": ParagraphStyle("small", parent=base, fontSize=8.5, leading=12, textColor=MUTED),
        "cell": ParagraphStyle("cell", parent=base, fontSize=9.5, leading=13),
        "cell_bold": ParagraphStyle("cell_bold", parent=base, fontName=f["bold"], fontSize=9.5, leading=13, textColor=INK),
        "head_cell": ParagraphStyle("head_cell", parent=base, fontName=f["label"], fontSize=9.5, leading=12, textColor=INK),
        "h3": ParagraphStyle("h3", parent=base, fontName=f["bold"], fontSize=14, leading=18, textColor=INK,
                             spaceBefore=12, spaceAfter=6),
        "headline": ParagraphStyle("headline", parent=base, fontName=f["bold"], fontSize=16, leading=21, textColor=INK),
        "finding_title": ParagraphStyle("finding_title", parent=base, fontName=f["bold"], fontSize=12.5, leading=16,
                                        textColor=INK),
        "bullet": ParagraphStyle("bullet", parent=base, leftIndent=14, bulletIndent=2, spaceAfter=5,
                                 bulletFontName=f["black"], bulletFontSize=12, bulletColor=YELLOW),
        "dark_body": ParagraphStyle("dark_body", parent=base, fontSize=12.5, leading=19, textColor=colors.white),
        "dark_offer": ParagraphStyle("dark_offer", parent=base, fontName=f["bold"], fontSize=17, leading=24,
                                     textColor=colors.white),
    }


# ---------------------------------------------------------------- flowables


class PillHeading(Flowable):
    """Yellow rounded heading, like "Project Overview" / "Scope of Work" in the Canva pages."""

    def __init__(self, text: str, size: float = 22, space_after: float = 14):
        super().__init__()
        self.text, self.size, self.space_after = _clean(text), size, space_after
        self.font = _fonts()["black"]
        self.pad_x, self.pad_y = 12, 7

    def wrap(self, avail_w, avail_h):
        text_w = pdfmetrics.stringWidth(self.text, self.font, self.size)
        self.box_w = min(avail_w, text_w + 2 * self.pad_x)
        self.box_h = self.size + 2 * self.pad_y
        return avail_w, self.box_h + self.space_after

    def draw(self):
        c = self.canv
        y = self.space_after
        c.setFillColor(YELLOW)
        c.roundRect(0, y, self.box_w, self.box_h, self.box_h / 2.4, stroke=0, fill=1)
        c.setFillColor(INK)
        c.setFont(self.font, self.size)
        c.drawString(self.pad_x, y + self.pad_y + self.size * 0.22, self.text)


class Tag(Flowable):
    """Small rounded label (severity, effort)."""

    def __init__(self, text: str, color: colors.Color, filled: bool = True):
        super().__init__()
        self.text, self.color, self.filled = _clean(text).upper(), color, filled
        self.font = _fonts()["label"]

    def wrap(self, avail_w, avail_h):
        self.w = pdfmetrics.stringWidth(self.text, self.font, 7.5) + 14
        return self.w, 14

    def draw(self):
        c = self.canv
        c.setFillColor(self.color)
        c.roundRect(0, 0, self.w, 14, 7, stroke=0, fill=1)
        c.setFillColor(colors.white)
        c.setFont(self.font, 7.5)
        c.drawString(7, 4.2, self.text)


class ScoreBadge(Flowable):
    """Overall score in a yellow circle."""

    def __init__(self, score, size: float = 30 * mm):
        super().__init__()
        self.score, self.size = score, size

    def wrap(self, avail_w, avail_h):
        return self.size, self.size

    def draw(self):
        c, r = self.canv, self.size / 2
        f = _fonts()
        c.setFillColor(YELLOW)
        c.circle(r, r, r, stroke=0, fill=1)
        c.setFillColor(INK)
        c.setFont(f["black"], 30)
        c.drawCentredString(r, r - 4, str(self.score) if self.score is not None else "-")
        c.setFont(f["label"], 7.5)
        c.drawCentredString(r, r - 16, "OUT OF 100")


class ScoreBar(Flowable):
    """Category and score on one line, a yellow bar on a grey track under it."""

    def __init__(self, label: str, score, note: str = ""):
        super().__init__()
        self.label, self.score, self.note = _clean(label), score, _clean(note)

    def wrap(self, avail_w, avail_h):
        self.w = avail_w
        return avail_w, 24

    def draw(self):
        c = self.canv
        f = _fonts()
        c.setFont(f["bold"], 10)
        c.setFillColor(INK)
        c.drawString(0, 13, self.label[:40])
        c.setFont(f["black"], 11)
        c.drawRightString(self.w, 13, str(self.score) if self.score is not None else "-")
        c.setFillColor(TRACK)
        c.roundRect(0, 1, self.w, 8, 4, stroke=0, fill=1)
        score = max(0, min(100, self.score or 0))
        if score:
            c.setFillColor(YELLOW)
            c.roundRect(0, 1, max(8, self.w * score / 100), 8, 4, stroke=0, fill=1)


# ---------------------------------------------------------------- data helpers (also used by tests)


def technical_checks(probe: dict, resources: dict | None = None) -> list[tuple[str, str, bool | None]]:
    """(label, measured value, ok) rows for the technical checks table."""
    if not probe:
        return []
    resources = resources or {}
    page = probe.get("page") or {}
    cache = (probe.get("cache_control") or "").lower()
    title_len = page.get("title_length", 0)
    desc_len = page.get("meta_description_length", 0)
    rows = [
        ("Secure connection (HTTPS)", "Yes" if probe.get("https") else "No", bool(probe.get("https"))),
        ("HSTS header", "Present" if probe.get("hsts") else "Missing", bool(probe.get("hsts"))),
        ("Server response time (TTFB)", f"{probe.get('ttfb_ms', 0)} ms", probe.get("ttfb_ms", 0) <= 800),
        ("HTML download time", f"{probe.get('load_ms', 0)} ms", probe.get("load_ms", 0) <= 2500),
        (
            "HTML size (transferred / uncompressed)",
            f"{round(probe.get('transfer_bytes', 0) / 1024)} KB / {round(probe.get('html_bytes', 0) / 1024)} KB",
            probe.get("html_bytes", 0) <= 150 * 1024,
        ),
        ("Text compression", probe.get("compression") or "None", bool(probe.get("compression"))),
        ("Browser caching header", probe.get("cache_control") or "Not set", bool(cache) and "no-store" not in cache),
        ("Redirects before page", str(probe.get("redirects", 0)), probe.get("redirects", 0) <= 1),
        ("Mobile viewport tag", "Present" if page.get("has_viewport") else "Missing", bool(page.get("has_viewport"))),
        ("Page title length", f"{title_len} characters", 10 <= title_len <= 65),
        ("Meta description length", f"{desc_len} characters", 50 <= desc_len <= 160),
        ("H1 headings", str(page.get("h1_count", 0)), page.get("h1_count", 0) == 1),
        (
            "Images missing alt text",
            f"{page.get('images_missing_alt', 0)} of {page.get('image_count', 0)}",
            page.get("images_missing_alt", 0) == 0,
        ),
        ("Render-blocking scripts in <head>", str(page.get("render_blocking_scripts", 0)), page.get("render_blocking_scripts", 0) == 0),
        ("Insecure (http://) resources", str(page.get("mixed_content_resources", 0)), page.get("mixed_content_resources", 0) == 0),
        ("Structured data (JSON-LD)", f"{page.get('structured_data_blocks', 0)} block(s)", page.get("structured_data_blocks", 0) > 0),
        (
            "Images without width/height",
            f"{page.get('images_without_dimensions', 0)} of {page.get('image_count', 0)}",
            page.get("images_without_dimensions", 0) == 0,
        ),
    ]
    if resources:
        total = resources.get("total_bytes") or 0
        rows += [
            ("Total page weight", f"{round(total / 1024)} KB in {resources.get('total_requests', 0)} files", total <= 2 * 1024 * 1024),
            ("Scripts/CSS sent uncompressed", str(len(resources.get("uncompressed_text_files") or [])), not resources.get("uncompressed_text_files")),
            ("Files with no or short caching", str(len(resources.get("short_or_no_cache") or [])), not resources.get("short_or_no_cache")),
            ("Images over 200 KB", str(len(resources.get("large_images") or [])), not resources.get("large_images")),
            ("Broken files (errors / 404s)", str(len(resources.get("failed") or [])), not resources.get("failed")),
        ]
    return rows


def _kb(value) -> str:
    if value is None:
        return "-"
    if value >= 1024 * 1024:
        return f"{value / (1024 * 1024):.1f} MB"
    return f"{max(round(value / 1024), 1 if value else 0)} KB"


TYPE_LABELS = {"script": "JavaScript", "stylesheet": "CSS", "image": "Images", "font": "Fonts", "media": "Video / audio"}


def page_weight_rows(resources: dict) -> list[tuple[str, str, str]]:
    """(label, files, size) rows summarising everything the homepage loads."""
    rows = [("HTML document", "1", _kb(resources.get("html_bytes")))]
    for key, label in TYPE_LABELS.items():
        bucket = (resources.get("by_type") or {}).get(key)
        if bucket:
            rows.append((label, str(bucket["count"]), _kb(bucket["bytes"])))
    rows.append(("Total", str(resources.get("total_requests", 0)), _kb(resources.get("total_bytes"))))
    return rows


def _short_url(url: str) -> str:
    name = url.split("?")[0].rstrip("/").split("/")[-1] or url
    return name if len(name) <= 60 else name[:57] + "..."



# ---------------------------------------------------------------- content blocks


def _table(rows: list[list], widths: list[float], *, top: bool = True) -> Table:
    """Table in the Canva style: yellow header row, light grid."""
    table = Table(rows, colWidths=widths, repeatRows=1)
    style = [
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("GRID", (0, 0), (-1, -1), 0.6, LINE),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ("BACKGROUND", (0, 0), (-1, 0), YELLOW),
    ]
    if top:
        style.append(("VALIGN", (0, 1), (-1, -1), "TOP"))
    table.setStyle(TableStyle(style))
    return table


def _status(ok: bool | None, styles) -> Paragraph:
    if ok is None:
        return Paragraph('<font color="#6B6B6B">Info</font>', styles["cell"])
    if ok:
        return Paragraph('<font color="#15803D"><b>Good</b></font>', styles["cell"])
    return Paragraph('<font color="#B45309"><b>Fix</b></font>', styles["cell"])


def _finding_block(finding: dict, styles) -> KeepTogether:
    label, color = SEVERITY.get(finding.get("severity", "medium"), SEVERITY["medium"])
    where = " | ".join(x for x in (finding.get("category"), finding.get("where")) if x)
    tags = Table([[Tag(label, color), Paragraph(_safe(where), styles["small"])]], colWidths=[None, None], hAlign="LEFT")
    tags.setStyle(TableStyle([("LEFTPADDING", (0, 0), (-1, -1), 0), ("RIGHTPADDING", (0, 0), (0, 0), 8),
                              ("VALIGN", (0, 0), (-1, -1), "MIDDLE"), ("TOPPADDING", (0, 0), (-1, -1), 0),
                              ("BOTTOMPADDING", (0, 0), (-1, -1), 0)]))
    body = [
        Paragraph(_safe(finding.get("title")), styles["finding_title"]),
        Spacer(1, 4),
        tags,
        Spacer(1, 6),
        Paragraph(f"<b>What I saw:</b> {_safe(finding.get('evidence'))}", styles["body"]),
        Spacer(1, 3),
        Paragraph(f"<b>Why it matters:</b> {_safe(finding.get('impact'))}", styles["body"]),
        Spacer(1, 3),
        Paragraph(f"<b>What I'd do:</b> {_safe(finding.get('recommendation'))}", styles["body"]),
    ]
    block = Table([["", body]], colWidths=[4, CONTENT_W - 4])
    block.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (0, 0), YELLOW),
        ("BACKGROUND", (1, 0), (1, 0), PANEL),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (0, 0), 0),
        ("RIGHTPADDING", (0, 0), (0, 0), 0),
        ("LEFTPADDING", (1, 0), (1, 0), 12),
        ("RIGHTPADDING", (1, 0), (1, 0), 12),
        ("TOPPADDING", (0, 0), (-1, -1), 10),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 11),
    ]))
    return KeepTogether([block, Spacer(1, 10)])


def _screenshots(screenshots: dict, styles) -> Table | None:
    """Desktop and phone first screens side by side, framed."""
    from reportlab.platypus import Image as PdfImage

    def scaled(path: str, max_w: float, max_h: float):
        try:
            image = PdfImage(_as_jpeg(path))
        except Exception:
            return None
        ratio = min(max_w / image.imageWidth, max_h / image.imageHeight)
        image.drawWidth, image.drawHeight = image.imageWidth * ratio, image.imageHeight * ratio
        return image

    def framed(image):
        frame = Table([[image]], colWidths=[image.drawWidth + 4])
        frame.setStyle(TableStyle([("BOX", (0, 0), (-1, -1), 1, INK), ("LEFTPADDING", (0, 0), (-1, -1), 2),
                                   ("RIGHTPADDING", (0, 0), (-1, -1), 2), ("TOPPADDING", (0, 0), (-1, -1), 2),
                                   ("BOTTOMPADDING", (0, 0), (-1, -1), 2)]))
        return frame

    desktop = (screenshots.get("desktop") or [None])[0]
    mobile = (screenshots.get("mobile") or [None])[0]
    gap = 8 * mm
    phone_w = CONTENT_W * 0.25
    desk_w = CONTENT_W - phone_w - gap
    cells, captions, widths = [], [], []
    if desktop and (img := scaled(desktop, desk_w - 6, 86 * mm)):
        cells.append(framed(img))
        captions.append(Paragraph("<b>Desktop</b>, first screen a visitor sees", styles["small"]))
        widths.append(desk_w + (gap if mobile else 0))
    if mobile and (img := scaled(mobile, phone_w - 6, 86 * mm)):
        cells.append(framed(img))
        captions.append(Paragraph("<b>Phone</b>, first screen", styles["small"]))
        widths.append(phone_w)
    if not cells:
        return None
    table = Table([cells, captions], colWidths=widths, hAlign="LEFT")
    table.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 0),
                               ("TOPPADDING", (0, 1), (-1, 1), 5)]))
    return table


def _as_jpeg(path: str, max_px: int = 1400) -> io.BytesIO:
    """Screenshots are PNGs of several MB; a JPEG keeps them sharp at a fraction of the size."""
    from PIL import Image

    with Image.open(path) as img:
        img = img.convert("RGB")
        img.thumbnail((max_px, max_px * 4))
        data = io.BytesIO()
        img.save(data, "JPEG", quality=80, optimize=True)
    data.seek(0)
    return data


def _short(text: str, limit: int) -> str:
    text = str(text or "").strip()
    if len(text) <= limit:
        return text
    cut = text[:limit].rsplit(" ", 1)[0].rstrip(",;:")
    return cut + "..."


# ---------------------------------------------------------------- page painters


def _brand(name: str) -> ImageReader | None:
    path = BRAND_DIR / name
    return ImageReader(str(path)) if path.exists() else None


def _full_page(canvas, name: str) -> bool:
    image = _brand(name)
    if not image:
        return False
    canvas.drawImage(image, 0, 0, PAGE_W, PAGE_H)
    return True


def _dark_background(canvas) -> None:
    if not _full_page(canvas, "dark_bg.jpg"):
        canvas.setFillColor(INK)
        canvas.rect(0, 0, PAGE_W, PAGE_H, stroke=0, fill=1)


def _fit_size(text: str, font: str, size: float, max_w: float, min_size: float = 14) -> float:
    while size > min_size and pdfmetrics.stringWidth(text, font, size) > max_w:
        size -= 1
    return size


def _pill(canvas, x: float, y: float, text: str, font: str, size: float = 11) -> None:
    """Yellow rounded label with dark text (PRESENTED TO / CONTACT ...)."""
    w = pdfmetrics.stringWidth(text, font, size) + 30
    h = size + 15
    canvas.setFillColor(YELLOW)
    canvas.roundRect(x, y, w, h, h / 2, stroke=0, fill=1)
    canvas.setFillColor(INK)
    canvas.setFont(font, size)
    canvas.drawString(x + 15, y + h / 2 - size * 0.36, text)


def _wrap_lines(text: str, font: str, size: float, max_w: float) -> list[str]:
    lines, line = [], ""
    for word in text.split():
        trial = f"{line} {word}".strip()
        if pdfmetrics.stringWidth(trial, font, size) <= max_w or not line:
            line = trial
        else:
            lines.append(line)
            line = word
    return lines + ([line] if line else [])


PLATFORM_STATUS = {
    "confirmed_missing": ("Missing", colors.HexColor("#DC2626")),
    "improvement": ("Improve", colors.HexColor("#D97706")),
    "manual_review": ("Worth checking", colors.HexColor("#2563EB")),
    "not_detected": ("Worth checking", colors.HexColor("#2563EB")),
}


def platform_intro(platform: dict) -> str:
    """One paragraph: what the site runs on and why that matters for the fixes."""
    name = platform.get("name") or ""
    addons = platform.get("addons") or []
    built_by = platform.get("built_by") or ""
    with_ = f" with {', '.join(addons[:-1]) + ' and ' + addons[-1] if len(addons) > 1 else addons[0]}" if addons else ""
    if name == "Custom-built":
        by = f" by {built_by}" if built_by else ""
        return (
            f"Your website is custom-built{by}{with_}, not on a ready-made platform. That means every change goes "
            "through your developer, so the suggestions below are written as clear, small requests you can pass on."
        )
    if not name or name == "Unknown":
        return ("We couldn't confirm which platform your website runs on, so these suggestions are based only on what "
                "we saw on the site.")
    return (
        f"Your website runs on {name}{with_}. The suggestions below use what {name} and those tools already offer, "
        "so most of them are settings or small changes rather than a rebuild."
    )


def _platform_block(item: dict, platform_name: str, styles) -> KeepTogether:
    label, color = PLATFORM_STATUS.get(item.get("status", ""), ("Improve", colors.HexColor("#D97706")))
    how = "How to do it" + (f" on {platform_name}" if platform_name and platform_name not in ("Unknown", "Custom-built") else "")
    if platform_name == "Custom-built":
        how = "What to ask your developer"
    body = [
        Paragraph(_safe(item.get("title")), styles["finding_title"]),
        Spacer(1, 4),
        Table([[Tag(label, color)]], hAlign="LEFT", style=TableStyle([("LEFTPADDING", (0, 0), (-1, -1), 0),
              ("TOPPADDING", (0, 0), (-1, -1), 0), ("BOTTOMPADDING", (0, 0), (-1, -1), 0)])),
        Spacer(1, 6),
        Paragraph(f"<b>What I saw:</b> {_safe(item.get('evidence'))}", styles["body"]),
        Spacer(1, 3),
        Paragraph(f"<b>{how}:</b> {_safe(item.get('recommendation'))}", styles["body"]),
    ]
    if item.get("business_relevance"):
        body += [Spacer(1, 3), Paragraph(f"<b>Why it matters:</b> {_safe(item.get('business_relevance'))}", styles["body"])]
    block = Table([["", body]], colWidths=[4, CONTENT_W - 4])
    block.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (0, 0), YELLOW),
        ("BACKGROUND", (1, 0), (1, 0), PANEL),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (0, 0), 0),
        ("RIGHTPADDING", (0, 0), (0, 0), 0),
        ("LEFTPADDING", (1, 0), (1, 0), 12),
        ("RIGHTPADDING", (1, 0), (1, 0), 12),
        ("TOPPADDING", (0, 0), (-1, -1), 10),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 11),
    ]))
    return KeepTogether([block, Spacer(1, 10)])


def build_audit_pdf(
    *,
    report: dict,
    performance: dict,
    lead: dict,
    sender: dict,
    generated_at: datetime,
    ai_model: str = "",
    screenshots: dict | None = None,
    platform: dict | None = None,
) -> bytes:
    """platform: {"name", "confidence_label", "addons": [...], "built_by"} for the "Your Website Platform" section."""
    f = _fonts()
    styles = _styles()
    buffer = io.BytesIO()
    business = _clean(lead.get("business_name") or "Your business")
    url = performance.get("url") or lead.get("website_url") or ""
    domain = _clean(extract_domain(url) or url)
    company = _clean(sender.get("company_name") or "Bee Creative")
    # Your website (AI & settings -> Company website), shown under "Presented by" on the cover
    company_url = (sender.get("company_website") or "").strip()
    company_site = re.sub(r"^https?://(www\.)?", "", company_url).rstrip("/")
    contact = _clean(sender.get("contact_email") or "")
    overall = report.get("overall_score")
    offer = report.get("free_offer") or {}

    def cover(canvas, doc):
        canvas.saveState()
        _dark_background(canvas)
        # Yellow title block from the left edge ("Website Proposal" in the Canva cover)
        canvas.setFillColor(YELLOW)
        canvas.rect(0, PAGE_H - 372, 437, 130, stroke=0, fill=1)
        canvas.setFillColor(INK)
        canvas.setFont(f["black"], 44)
        canvas.drawString(17, PAGE_H - 325, "Website Audit")
        # Business name in the rounded bar
        bar_top, bar_h = PAGE_H - 383, 90
        canvas.setFillColor(YELLOW)
        canvas.roundRect(-bar_h, bar_top - bar_h, 504 + bar_h, bar_h, bar_h / 2, stroke=0, fill=1)
        name = business.upper()
        size = _fit_size(name, f["black"], 30, 430, min_size=15)
        canvas.setFillColor(INK)
        canvas.setFont(f["black"], size)
        canvas.drawString(52, bar_top - bar_h / 2 - size * 0.35, name)
        # Date and website
        canvas.setFillColor(colors.white)
        canvas.setFont(f["label"], 16)
        canvas.drawString(62, PAGE_H - 530, generated_at.strftime("%d.%m.%Y"))
        canvas.setFont(f["body"], 12)
        canvas.drawString(62, PAGE_H - 550, domain[:60])
        # Presented to / by
        _pill(canvas, 62, 118, "PRESENTED TO", f["label"], 10.5)
        _pill(canvas, 296, 118, "PRESENTED BY", f["label"], 10.5)
        canvas.setFillColor(colors.white)
        to_size = _fit_size(business.upper(), f["black"], 17, 215, min_size=10)
        canvas.setFont(f["black"], to_size)
        canvas.drawString(62, 90, business.upper())
        canvas.setFont(f["black"], 19)
        canvas.drawString(296, 90, company)
        if company_site:
            canvas.setFont(f["body"], 12)
            canvas.drawString(296, 70, company_site[:40])
            width = canvas.stringWidth(company_site[:40], f["body"], 12)
            href = company_url if company_url.startswith("http") else f"https://{company_url}"
            canvas.linkURL(href, (296, 66, 296 + width, 82), relative=0)  # clickable in the PDF
        canvas.restoreState()

    def static_page(name):
        def paint(canvas, doc):
            canvas.saveState()
            if not _full_page(canvas, name):
                _dark_background(canvas)
            canvas.restoreState()
        return paint

    def white_page(canvas, doc):
        canvas.saveState()
        canvas.setFillColor(colors.white)
        canvas.rect(0, 0, PAGE_W, PAGE_H, stroke=0, fill=1)
        logo = _brand("logo_black.png")
        if logo:
            w = 30 * mm
            iw, ih = logo.getSize()
            h = w * ih / iw
            canvas.drawImage(logo, (PAGE_W - w) / 2, PAGE_H - LOGO_TOP - h, w, h, mask="auto")
        canvas.setFont(f["label"], 7.5)
        canvas.setFillColor(MUTED)
        canvas.drawString(MARGIN, 11 * mm, _clean(f"{company.upper()} | WEBSITE AUDIT | {domain.upper()}"))
        canvas.drawRightString(PAGE_W - MARGIN, 11 * mm, f"PAGE {doc.page:02d}")
        canvas.restoreState()

    def offer_page(canvas, doc):
        canvas.saveState()
        _dark_background(canvas)
        x, width = 62, PAGE_W - 62 - 90
        canvas.setFont(f["black"], 42)
        canvas.setFillColor(colors.white)
        canvas.drawString(x, PAGE_H - 290, "A Free")
        canvas.setFillColor(YELLOW)
        canvas.drawString(x + pdfmetrics.stringWidth("A Free ", f["black"], 42), PAGE_H - 290, "Offer")
        canvas.setFillColor(colors.white)
        canvas.setFont(f["black"], 24)
        canvas.drawString(x, PAGE_H - 325, _clean(f"for {business}")[:48])
        y = PAGE_H - 380
        _pill(canvas, x, y, "WHAT YOU GET", f["label"], 11)
        y -= 34
        canvas.setFillColor(colors.white)
        canvas.setFont(f["bold"], 16.5)
        for line in _wrap_lines(_clean(offer.get("offer") or ""), f["bold"], 16.5, width)[:5]:
            canvas.drawString(x, y, line)
            y -= 23
        if offer.get("why"):
            y -= 20
            _pill(canvas, x, y, "WHY THIS ONE", f["label"], 11)
            y -= 32
            canvas.setFillColor(colors.white)
            canvas.setFont(f["body"], 12.5)
            for line in _wrap_lines(_clean(offer["why"]), f["body"], 12.5, width)[:6]:
                canvas.drawString(x, y, line)
                y -= 18
        y -= 22
        _pill(canvas, x, y, "HOW TO GET IT", f["label"], 11)
        y -= 32
        claim = "Just reply to the email this report came with. No cost and no obligation."
        if contact:
            claim = f"Just reply to the email this report came with, or write to {contact}. No cost and no obligation."
        canvas.setFillColor(colors.white)
        canvas.setFont(f["body"], 12.5)
        for line in _wrap_lines(_clean(claim), f["body"], 12.5, width):
            canvas.drawString(x, y, line)
            y -= 18
        canvas.restoreState()

    doc = BaseDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=MARGIN,
        rightMargin=MARGIN,
        topMargin=BODY_TOP,
        bottomMargin=20 * mm,
        title=_clean(f"Website audit - {business}"),
        author=company,
        subject=_clean(f"Website audit for {domain}"),
    )
    full = Frame(0, 0, PAGE_W, PAGE_H, id="full", leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0)
    body_frame = Frame(MARGIN, 20 * mm, CONTENT_W, PAGE_H - BODY_TOP - 20 * mm, id="body")
    templates = [PageTemplate(id="cover", frames=[full], onPage=cover),
                 PageTemplate(id="white", frames=[body_frame], onPage=white_page),
                 PageTemplate(id="offer", frames=[full], onPage=offer_page)]
    for name in INTRO_PAGES + OUTRO_PAGES:
        templates.append(PageTemplate(id=name, frames=[full], onPage=static_page(name)))
    doc.addPageTemplates(templates)

    story: list = []

    def full_page(template: str) -> None:
        story.extend([NextPageTemplate(template), PageBreak(), Spacer(1, 1)])

    # Company introduction pages straight after the cover
    for name in INTRO_PAGES:
        if (BRAND_DIR / name).exists():
            full_page(name)
    story.extend([NextPageTemplate("white"), PageBreak()])

    # ---- Your website at a glance
    story.append(PillHeading("Your Website at a Glance"))
    intro = [Paragraph(_safe(report.get("headline") or ""), styles["headline"]), Spacer(1, 6)]
    if report.get("first_impression"):
        intro.append(Paragraph(_safe(report["first_impression"]), styles["lead"]))
    glance = Table([[ScoreBadge(overall), intro]], colWidths=[36 * mm, CONTENT_W - 36 * mm])
    glance.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 0),
                                ("RIGHTPADDING", (0, 0), (0, 0), 6 * mm)]))
    story += [glance, Spacer(1, 12), Paragraph(_safe(report.get("executive_summary")), styles["body"])]
    # ---- How the website looks today, then the category scores (they fit under the screenshots)
    shots = _screenshots(screenshots or {}, styles)
    if shots:
        story += [CondPageBreak(150 * mm), PillHeading("How Your Website Looks Today"),
                  Paragraph(f"This is what a new visitor sees first when they open {_safe(domain)}.", styles["body"]),
                  Spacer(1, 10), shots, Spacer(1, 6)]
    categories = report.get("category_scores") or []
    if categories:
        # Two columns: label + score, bar, one-line note; half the height of a single column.
        cells = []
        for item in categories[:6]:
            cell = [ScoreBar(item.get("category") or "", item.get("score"))]
            if item.get("summary"):
                cell.append(Paragraph(_safe(_short(item["summary"], 110)), styles["small"]))
            cells.append(cell)
        rows = [cells[i:i + 2] + [""] * (2 - len(cells[i:i + 2])) for i in range(0, len(cells), 2)]
        grid = Table(rows, colWidths=[CONTENT_W / 2] * 2)
        grid.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 0),
                                  ("RIGHTPADDING", (0, 0), (0, -1), 9 * mm), ("RIGHTPADDING", (1, 0), (1, -1), 0),
                                  ("TOPPADDING", (0, 0), (-1, -1), 0), ("BOTTOMPADDING", (0, 0), (-1, -1), 9)]))
        story += [CondPageBreak(60 * mm), KeepTogether([Paragraph("Scores at a glance", styles["h3"]), grid])]

    # ---- Findings
    findings = report.get("findings") or []
    if findings:
        story += [PageBreak(), PillHeading("What I Found")]
        story += [_finding_block(item, styles) for item in findings]

    # ---- Your website platform: what it runs on, and platform-native fixes
    opportunities = [o for o in report.get("platform_opportunities") or [] if o.get("title") and o.get("recommendation")]
    platform = platform or {}
    platform_name = platform.get("name") or report.get("platform") or ""
    if opportunities or (platform_name and platform_name != "Unknown"):
        story += [CondPageBreak(90 * mm), PillHeading("Your Website Platform"),
                  Paragraph(_safe(platform_intro({**platform, "name": platform_name})), styles["body"]), Spacer(1, 10)]
        story += [_platform_block(item, platform_name, styles) for item in opportunities[:5]]

    # ---- What works + action plan
    strengths = report.get("strengths") or []
    plan = report.get("action_plan") or []
    if strengths:
        story += [CondPageBreak(70 * mm), PillHeading("What's Already Working")]
        story += [Paragraph(_safe(item), styles["bullet"], bulletText="•") for item in strengths]
        story.append(Spacer(1, 14))
    if plan:
        rows = [[Paragraph("Step", styles["head_cell"]), Paragraph("What to do", styles["head_cell"]),
                 Paragraph("Why it helps", styles["head_cell"]), Paragraph("Effort", styles["head_cell"])]]
        for index, item in enumerate(plan, start=1):
            rows.append([
                Paragraph(str(index), styles["cell_bold"]),
                Paragraph(_safe(item.get("action")), styles["cell"]),
                Paragraph(_safe(item.get("expected_impact")), styles["cell"]),
                Paragraph(_safe(str(item.get("effort", "")).capitalize()), styles["cell"]),
            ])
        story += [CondPageBreak(80 * mm), PillHeading("Action Plan"),
                  _table(rows, [CONTENT_W * 0.08, CONTENT_W * 0.44, CONTENT_W * 0.34, CONTENT_W * 0.14])]
    if report.get("conclusion"):
        story.append(KeepTogether([Paragraph("My take", styles["h3"]), Paragraph(_safe(report["conclusion"]), styles["body"])]))

    # ---- Under the hood (only measured data)
    resources = performance.get("resources") or {}
    checks = technical_checks(performance["probe"], resources) if performance.get("probe") else []
    if resources or checks:
        story += [CondPageBreak(90 * mm), PillHeading("Under the Hood")]
    if resources:
        rows = [[Paragraph("What the homepage loads", styles["head_cell"]), Paragraph("Files", styles["head_cell"]),
                 Paragraph("Size", styles["head_cell"])]]
        for label, count, size in page_weight_rows(resources):
            style = styles["cell_bold"] if label == "Total" else styles["cell"]
            rows.append([Paragraph(_safe(label), style), Paragraph(count, style), Paragraph(size, style)])
        story += [_table(rows, [CONTENT_W * 0.55, CONTENT_W * 0.15, CONTENT_W * 0.30], top=False), Spacer(1, 12)]
    if checks:
        rows = [[Paragraph("Check", styles["head_cell"]), Paragraph("Measured", styles["head_cell"]),
                 Paragraph("Result", styles["head_cell"])]]
        for label, value, ok in checks:
            rows.append([Paragraph(_safe(label), styles["cell"]), Paragraph(_safe(value), styles["cell"]),
                         _status(ok, styles)])
        story.append(_table(rows, [CONTENT_W * 0.52, CONTENT_W * 0.33, CONTENT_W * 0.15], top=False))
    method = (
        f"How this audit was made: on {generated_at:%d %B %Y} I opened {_safe(url)} on a desktop and a phone screen, "
        "took screenshots, and measured response time, file sizes, compression and caching for every file the "
        "homepage loads. Timings vary a little between visits and locations."
    )
    story += [Spacer(1, 14), Paragraph(method, styles["small"])]

    # ---- Sites the team built for businesses like this one (from the portfolio sheet)
    references = report.get("reference_sites") or []
    if references:
        story += [CondPageBreak(90 * mm), Spacer(1, 16), PillHeading("Work We've Done", space_after=8),
                  Paragraph("A few sites our team built for businesses like yours.", styles["body"]), Spacer(1, 12)]
        for item in references:
            url = str(item.get("url") or "")
            shown = _safe(url.removeprefix("https://").removeprefix("http://").rstrip("/"))
            block = [
                Tag(item.get("niche") or "Portfolio", INK),
                Spacer(1, 5),
                Paragraph(f'<a href="{_safe(url)}"><b>{shown}</b></a>', styles["finding_title"]),
            ]
            if item.get("note"):
                block += [Spacer(1, 2), Paragraph(_safe(item["note"]), styles["body"])]
            story += [KeepTogether(block + [Spacer(1, 12)])]

    # ---- Free offer + company pages
    if offer.get("offer"):
        full_page("offer")
    for name in OUTRO_PAGES:
        if (BRAND_DIR / name).exists():
            full_page(name)

    doc.build(story)
    return buffer.getvalue()


def pdf_filename(business_name: str, generated_at: datetime) -> str:
    slug = "".join(ch if ch.isalnum() else "-" for ch in (business_name or "website").lower()).strip("-")
    while "--" in slug:
        slug = slug.replace("--", "-")
    return f"website-audit-{slug[:50] or 'website'}-{generated_at:%Y-%m-%d}.pdf"
