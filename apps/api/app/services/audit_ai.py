"""Claude-generated audit report and outreach email, grounded in measured data."""

from __future__ import annotations

import json
import re

from app.logging_config import get_logger
from app.services.ai import AIProvider, AIProviderError

logger = get_logger("audit_ai")

SEVERITIES = ["critical", "high", "medium", "low"]
EFFORTS = ["low", "medium", "high"]

AUDIT_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "overall_score": {"type": "integer"},
        "headline": {"type": "string"},
        "first_impression": {"type": "string"},
        "executive_summary": {"type": "string"},
        "category_scores": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "category": {"type": "string"},
                    "score": {"type": "integer"},
                    "summary": {"type": "string"},
                },
                "required": ["category", "score", "summary"],
                "additionalProperties": False,
            },
        },
        "strengths": {"type": "array", "items": {"type": "string"}},
        "findings": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "category": {"type": "string"},
                    "where": {"type": "string"},
                    "severity": {"type": "string", "enum": SEVERITIES},
                    "evidence": {"type": "string"},
                    "impact": {"type": "string"},
                    "recommendation": {"type": "string"},
                },
                "required": ["title", "category", "where", "severity", "evidence", "impact", "recommendation"],
                "additionalProperties": False,
            },
        },
        "action_plan": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "priority": {"type": "integer"},
                    "action": {"type": "string"},
                    "expected_impact": {"type": "string"},
                    "effort": {"type": "string", "enum": EFFORTS},
                },
                "required": ["priority", "action", "expected_impact", "effort"],
                "additionalProperties": False,
            },
        },
        "conclusion": {"type": "string"},
        "free_offer": {
            "type": "object",
            "properties": {"offer": {"type": "string"}, "why": {"type": "string"}},
            "required": ["offer", "why"],
            "additionalProperties": False,
        },
        "reference_sites": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"url": {"type": "string"}, "note": {"type": "string"}},
                "required": ["url", "note"],
                "additionalProperties": False,
            },
        },
    },
    "required": [
        "overall_score",
        "headline",
        "first_impression",
        "executive_summary",
        "category_scores",
        "strengths",
        "findings",
        "action_plan",
        "conclusion",
        "free_offer",
        "reference_sites",
    ],
    "additionalProperties": False,
}

EMAIL_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "subject": {"type": "string"},
        "body_html": {"type": "string"},
    },
    "required": ["subject", "body_html"],
    "additionalProperties": False,
}

# Shared by the report and the email: the owner should feel a person looked at their site, not a template.
HUMAN_STYLE = """Writing style (important):
- Write like an experienced consultant talking to the owner: first person ("I noticed", "when I opened it on my
  phone"), plain everyday words, contractions are fine. Mix short sentences with longer ones.
- Be concrete: name the actual button, headline, photo, page or number you saw. Prefer one specific detail over a
  general statement.
- Avoid phrases that sound machine-written: "delve", "elevate", "seamless", "robust", "leverage", "unlock",
  "game-changer", "cutting-edge", "in today's digital landscape", "it's worth noting", "not only ... but also",
  "whether you're ... or ...", "a testament to", "look no further". No em dashes. No groups of three adjectives,
  no rhetorical questions, no exclamation marks, no emojis, no empty compliments.
- Don't start several sentences or bullets the same way. Don't summarise what you just said.
"""

AUDIT_SYSTEM = """You are a senior conversion-rate-optimisation (CRO) and UX/web-design consultant. You review a small
business's website the way an experienced human expert would, and write a client-facing audit that will be
rendered as a PDF and sent to the owner.

You are given screenshots of the live site (desktop and phone, first screen first) plus measured data: response
time, page weight, every file the homepage loads, the HTML source and a crawl of a few pages.

Look at the screenshots first and judge them as a first-time visitor, then use the data to back it up:
- First impression (5-second test): is it instantly clear what the business does, for whom, and where?
- Value proposition and headline: specific and benefit-led, or vague?
- Visual design: hierarchy, layout and spacing, typography and readability, colour and contrast, imagery quality,
  consistency, whether it looks modern and trustworthy.
- Calls to action: is there one obvious next step above the fold? Button visibility, wording and placement;
  phone number / booking / quote easy to find and tap.
- Trust and credibility: reviews, ratings, testimonials, accreditations, guarantees, real photos, contact details.
- Friction: clutter, competing CTAs, long or confusing forms, pop-ups, missing information.
- Mobile: readability, tap targets, layout breaking, sticky CTA or click-to-call, what fits on the first screen.
- Speed and technical basics, only where the measurements show a real problem.

Ground rules:
- Base every finding on what is visible in the screenshots or present in the data; describe what you saw
  (e.g. "the hero has two equally weighted buttons", "no reviews on the first two phone screens").
- Never invent traffic, conversion rates, revenue, rankings, competitors or problems you cannot see.
- If there are no screenshots, say the visual review was not possible and rely on the data.
- Scores are your expert assessment, 0-100: 90+ strong, 50-89 needs work, under 50 poor. overall_score reflects
  the category scores, weighted towards design and conversion.
- Plain language for a non-technical owner; concrete, specific recommendations.
- first_impression: 2-3 sentences, what a visitor understands (or not) in the first 5 seconds.
- category_scores: 5-6 of Design, Conversion (CRO), Mobile experience, Trust & credibility, Speed, SEO basics.
- findings: 5-9 items, most severe first. category is one of the above; where names the page area
  (e.g. "Desktop hero", "Phone header", "Contact form"); evidence describes what you saw or measured.
- action_plan: 3-6 steps ordered by impact (1 = do first). strengths: 2-4 genuine positives.
- headline: one sentence, max 14 words. executive_summary: 90-150 words. conclusion: 40-80 words.
- free_offer: pick ONE of the free_offers you are given, the one that best fixes this site's biggest problem.
  offer: one sentence describing exactly what this business would get (e.g. "A redesigned first screen for
  abcplumbing.com in Figma, with your phone number and a clear 'Book a plumber' button"). why: 1-2 sentences tying
  it to the findings above.
- reference_sites: 1-3 sites from the portfolio you are given (sites the sender's team built), closest to this
  business's niche first (same niche, then the nearest one: e.g. a skincare brand -> Skin Care / Beauty, then Body
  Care). Copy each url exactly as given. note: one short sentence on why it is relevant (same industry, similar
  products, similar audience). If nothing is close, pick the strongest general example and say so in the note.
  Empty list only if no portfolio is given.
""" + HUMAN_STYLE

EMAIL_SYSTEM = """You write short cold emails that a real person would write, sent with a website audit PDF.
Rules:
- Greet recipient_business.contact_name by first name when it is given; otherwise greet the business's team by name.
- Open with one specific thing you noticed on their site (from the audit), not with who you are.
- Mention 1-2 more findings in terms of this business (its name, services or location), so the email could not be
  sent unchanged to anyone else. The full audit PDF is attached; say so once, briefly.
- Make ONE free offer: the one in audit.free_offer when it is given (the PDF's offer page describes the same
  offer, so use it); otherwise choose from sender.free_offers the one that best fixes this site's biggest problem.
  Describe concretely what they would get for their business, and that there is no cost or obligation.
- When audit.reference_site is given, add one natural sentence about it: work the sender's team did for a
  business like theirs, e.g. "We recently built https://example.com for another skincare brand." Write the URL out
  in full as the visible link text: <a href="URL">URL</a>. Describe it plainly (same industry / similar products);
  never claim results or numbers for it.
- End with a simple yes/no question about the offer (e.g. "Want me to put it together?"), then sign off. When
  sender.signature_added is true, the sign-off is only a short closing such as "Best," with nothing after it (the
  sender's signature with name and company is added underneath automatically); otherwise sign off with the
  sender's name and company.
- No invented facts, no flattery, no fake urgency, no claims about results or rankings.
- 90-160 words. body_html: simple <p> paragraphs only (no <html>, <head>, styles or images; the reference site is
  the only link), no unsubscribe line, no P.S.
- subject: under 50 characters, lower-key and specific to the business (like a note from a person), no clickbait,
  no ALL CAPS, no exclamation marks.
""" + HUMAN_STYLE


def parse_json_text(text: str) -> dict:
    """Parse model output into a dict, tolerating code fences or leading prose."""
    cleaned = (text or "").strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", cleaned, re.DOTALL)
    if fence:
        cleaned = fence.group(1).strip()
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError:
        start, end = cleaned.find("{"), cleaned.rfind("}")
        if start == -1 or end <= start:
            raise AIProviderError("The AI response was not valid JSON.") from None
        try:
            data = json.loads(cleaned[start : end + 1])
        except json.JSONDecodeError as exc:
            raise AIProviderError("The AI response was not valid JSON.") from exc
    if not isinstance(data, dict):
        raise AIProviderError("The AI response was not a JSON object.")
    return data


def _clamp_score(value) -> int | None:
    try:
        return max(0, min(100, int(round(float(value)))))
    except (TypeError, ValueError):
        return None


def _text(value, limit: int = 2000) -> str:
    # Em / en dashes are a common giveaway of machine-written text; keep the writing plain.
    text = str(value or "").replace(" \u2014 ", ", ").replace("\u2014", ", ").replace(" \u2013 ", " - ")
    return text.strip()[:limit]


def normalize_report(data: dict) -> dict:
    findings = []
    for item in data.get("findings") or []:
        if not isinstance(item, dict) or not _text(item.get("title")):
            continue
        severity = _text(item.get("severity")).lower()
        findings.append(
            {
                "title": _text(item.get("title"), 200),
                "category": _text(item.get("category"), 80),
                "where": _text(item.get("where"), 80),
                "severity": severity if severity in SEVERITIES else "medium",
                "evidence": _text(item.get("evidence"), 600),
                "impact": _text(item.get("impact"), 600),
                "recommendation": _text(item.get("recommendation"), 800),
            }
        )
    findings.sort(key=lambda f: SEVERITIES.index(f["severity"]))

    plan = []
    for index, item in enumerate(data.get("action_plan") or [], start=1):
        if not isinstance(item, dict) or not _text(item.get("action")):
            continue
        effort = _text(item.get("effort")).lower()
        try:
            priority = int(item.get("priority"))
        except (TypeError, ValueError):
            priority = index
        plan.append(
            {
                "priority": priority,
                "action": _text(item.get("action"), 400),
                "expected_impact": _text(item.get("expected_impact"), 400),
                "effort": effort if effort in EFFORTS else "medium",
            }
        )
    plan.sort(key=lambda p: p["priority"])

    categories = []
    for item in data.get("category_scores") or []:
        if not isinstance(item, dict) or not _text(item.get("category")):
            continue
        categories.append(
            {
                "category": _text(item.get("category"), 60),
                "score": _clamp_score(item.get("score")),
                "summary": _text(item.get("summary"), 300),
            }
        )

    report = {
        "overall_score": _clamp_score(data.get("overall_score")),
        "headline": _text(data.get("headline"), 200),
        "first_impression": _text(data.get("first_impression"), 800),
        "executive_summary": _text(data.get("executive_summary"), 3000),
        "category_scores": categories,
        "strengths": [_text(s, 300) for s in data.get("strengths") or [] if _text(s)],
        "findings": findings,
        "action_plan": plan,
        "conclusion": _text(data.get("conclusion"), 1500),
        "free_offer": {
            "offer": _text((data.get("free_offer") or {}).get("offer"), 400),
            "why": _text((data.get("free_offer") or {}).get("why"), 600),
        },
        "reference_sites": [
            {"url": _text(item.get("url"), 300), "note": _text(item.get("note"), 300)}
            for item in data.get("reference_sites") or []
            if isinstance(item, dict) and _text(item.get("url"))
        ][:3],
    }
    if not report["executive_summary"] or not report["findings"]:
        raise AIProviderError("The AI audit was missing its summary or findings.")
    return report


def build_audit_context(
    *, lead: dict, site: dict, performance: dict, free_offers: list[str] | None = None, portfolio: dict | None = None
) -> str:
    payload = {
        "business": lead,
        "website_crawl": site,
        "measurements": performance,
        "free_offers": free_offers or [],
        "portfolio": portfolio or {},  # {niche: [urls]} of sites the sender's team built
    }
    return (
        "Write the website audit for this business from the data below.\n"
        "Return JSON only, matching the requested schema.\n\n"
        f"{json.dumps(payload, sort_keys=True, default=str, ensure_ascii=False)}"
    )


def generate_audit_report(
    provider: AIProvider,
    *,
    lead: dict,
    site: dict,
    performance: dict,
    timeout: float,
    images: list[str] | None = None,
    free_offers: list[str] | None = None,
    portfolio: dict | None = None,
) -> dict:
    prompt = build_audit_context(
        lead=lead, site=site, performance=performance, free_offers=free_offers, portfolio=portfolio
    )
    text = provider.generate(
        prompt,
        system=AUDIT_SYSTEM,
        temperature=0.2,
        max_tokens=16000,
        json_schema=AUDIT_SCHEMA,
        timeout=timeout,
        images=images or None,
    )
    report = normalize_report(parse_json_text(text))
    logger.info("audit_report_generated", findings=len(report["findings"]), score=report["overall_score"])
    return report


def generate_audit_email(
    provider: AIProvider, *, lead: dict, report: dict, sender: dict, timeout: float
) -> dict:
    audit_summary = {
        "overall_score": report.get("overall_score"),
        "headline": report.get("headline"),
        "first_impression": report.get("first_impression"),
        "executive_summary": report.get("executive_summary"),
        "top_findings": report.get("findings", [])[:4],
        "first_actions": report.get("action_plan", [])[:3],
        "free_offer": report.get("free_offer") or {},
        "reference_site": (report.get("reference_sites") or [None])[0],
    }
    prompt = (
        "Write the outreach email that accompanies the attached audit PDF.\n"
        "Return JSON only with keys subject and body_html.\n\n"
        + json.dumps(
            {"recipient_business": lead, "audit": audit_summary, "sender": sender},
            sort_keys=True,
            default=str,
            ensure_ascii=False,
        )
    )
    text = provider.generate(
        prompt,
        system=EMAIL_SYSTEM,
        temperature=0.4,
        max_tokens=4000,
        json_schema=EMAIL_SCHEMA,
        timeout=timeout,
    )
    data = parse_json_text(text)
    subject = _text(data.get("subject"), 200)
    body = _text(data.get("body_html"), 6000)
    if not body:
        raise AIProviderError("The AI email draft was empty.")
    if "<p" not in body.lower():
        body = "".join(f"<p>{line.strip()}</p>" for line in body.split("\n\n") if line.strip())
    if not subject:
        subject = f"Website audit for {lead.get('business_name') or 'your website'}"
    return {"subject": subject, "html": body}
