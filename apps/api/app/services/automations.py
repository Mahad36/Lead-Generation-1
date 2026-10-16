"""Automation offers: which automation to propose to a business, from its website and its type.

Two inputs, in this order:
  1. the website analysis (evidence): what the pages show, e.g. bookings only by phone, a contact form with no
     instant answer, a quote form, no chat, no reviews from a review platform
  2. the business type (fit): the same gap needs a different fix per industry (a dentist books patients with
     reminders; a roofer needs an instant reply and follow-ups on quotes)

Only automations you offer (AI & settings -> Automations) are ever proposed. A "seen" candidate rests on something
the audit observed; when nothing fits, the industry's most common need becomes a "question" to ask, never a claim.
Widgets loaded by scripts can be invisible in the page source, so the wording is "I couldn't find...", not
"you don't have...".
"""

from __future__ import annotations

import re

# key: (name, short description for prompts / the PDF)
AUTOMATIONS: dict[str, tuple[str, str]] = {
    "online_booking": (
        "Online booking with automatic reminders",
        "customers book a time on the website any hour; confirmations and reminder texts / emails go out by themselves",
    ),
    "instant_reply": (
        "Instant reply to every enquiry",
        "every form or quote request gets a personal reply within a minute, day or night, with the next step",
    ),
    "ai_assistant": (
        "AI assistant on the website",
        "answers common questions and takes bookings or enquiries 24/7, handing over to the team when needed",
    ),
    "review_requests": (
        "Automatic review requests",
        "after each visit or job, the customer gets a short message asking for a Google review",
    ),
    "quote_followup": (
        "Automatic quote follow-ups",
        "quotes that haven't been accepted get a friendly follow-up after a few days, so fewer jobs go cold",
    ),
    "missed_call_textback": (
        "Missed-call text-back",
        "a missed call gets an instant text with a booking or callback link, so the caller doesn't ring a competitor",
    ),
}
ALL_KEYS = list(AUTOMATIONS)

# Business type -> automations in order of fit (first match wins)
INDUSTRIES: list[tuple[tuple[str, ...], list[str]]] = [
    (("dent", "orthodont", "endodont", "periodont", "oral surg"), ["online_booking", "review_requests", "missed_call_textback", "ai_assistant"]),
    (("med spa", "medspa", "aesthetic", "dermatolog", "cosmetic surg", "plastic surg", "botox", "laser"), ["ai_assistant", "online_booking", "review_requests", "missed_call_textback"]),
    (("roof", "hvac", "heating", "air condition", "plumb", "electric", "solar", "remodel", "contractor", "landscap",
      "pest", "garage door", "fenc", "paint", "floor", "mover", "moving", "cleaning", "pool", "window", "foundation",
      "handyman", "locksmith", "tree", "builder", "construction", "kitchen", "bathroom", "gutter", "concrete"),
     ["instant_reply", "quote_followup", "missed_call_textback", "review_requests"]),
    (("law", "lawyer", "attorney", "solicitor", "legal"), ["instant_reply", "ai_assistant", "review_requests", "online_booking"]),
    (("jewel", "watch"), ["review_requests", "online_booking", "ai_assistant", "instant_reply"]),
    (("salon", "barber", "nail", "beauty", "spa", "tattoo", "massage", "gym", "yoga", "pilates", "trainer", "fitness", "lash", "brow"),
     ["online_booking", "missed_call_textback", "review_requests", "ai_assistant"]),
    (("restaurant", "cafe", "café", "bakery", "patisserie", "pastry", "bar", "pizza", "catering"),
     ["review_requests", "online_booking", "missed_call_textback", "ai_assistant"]),
    (("real estate", "realtor", "estate agent", "mortgage", "insurance", "financial", "accountant", "tax", "bookkeep"),
     ["instant_reply", "ai_assistant", "online_booking", "review_requests"]),
    (("clinic", "chiropract", "physio", "vet", "optomet", "optician", "hearing", "fertility", "doctor", "medical", "therap", "podiat"),
     ["online_booking", "review_requests", "ai_assistant", "missed_call_textback"]),
    (("hotel", "b&b", "bed and breakfast", "tour", "travel", "wedding", "venue", "event", "photograph"),
     ["ai_assistant", "instant_reply", "review_requests", "online_booking"]),
]
DEFAULT_ORDER = ["instant_reply", "review_requests", "ai_assistant", "online_booking"]

BOOKING_TOOLS = (
    "calendly", "acuityscheduling", "setmore", "fresha", "vagaro", "zocdoc", "nexhealth", "localmed", "squareup.com/appointments",
    "booksy", "mindbodyonline", "jane.app", "simplybook", "youcanbook", "opentable", "resy.com", "sevenrooms",
    "housecallpro", "servicetitan", "jobber", "flexbook", "solutionreach", "lighthouse360", "weave", "doctible",
    "patientpop", "zenoti", "boulevard", "glossgenius", "schedulicity", "appointy", "booker.com", "tebra", "denticon",
    "dentalhub", "yapi", "modento", "book.getweave", "phorest", "timely", "bookwhen", "picktime", "tidycal", "hubspot.com/meetings",
)
CHAT_TOOLS = (
    "tawk.to", "livechatinc", "intercom", "drift.com", "crisp.chat", "tidio", "zdassets", "zopim", "js.usemessages",
    "podium", "birdeye", "manychat", "botpress", "chatbase", "smith.ai", "olark", "freshchat", "livechat", "userlike",
    "chat-widget", "chatwidget", "gorgias", "kommunicate", "landbot", "voiceflow", "webchat", "leadconnector",
)
REVIEW_TOOLS = (
    "birdeye", "podium", "trustindex", "elfsight", "grade.us", "trustpilot", "reviews.io", "judge.me", "yotpo",
    "embedsocial", "reviewsonmywebsite", "google-reviews", "googlereviews", "featurable", "nicejob", "broadly",
)
QUOTE_WORDS = re.compile(r"(free (quote|estimate)|get a (quote|estimate)|request a (quote|estimate)|quote request|estimate request)", re.I)
BOOK_WORDS = re.compile(r"(book (now|online|an appointment|a consultation|your)|request an appointment|schedule (a|an|your) (visit|appointment|consultation))", re.I)


def website_signals(pages_html: list[str], addons: list[dict] | None = None) -> dict:
    """What the pages show that matters for automation (from the HTML the audit fetched)."""
    html = " ".join(h for h in pages_html if h)
    low = html.lower()
    categories = {a.get("category") for a in addons or []}
    text = re.sub(r"<script.*?</script>|<style.*?</style>|<[^>]+>", " ", html, flags=re.S | re.I)
    return {
        "booking_tool": next((t for t in BOOKING_TOOLS if t in low), "") or ("booking" if "booking" in categories else ""),
        "booking_cta": bool(BOOK_WORDS.search(text)),
        "chat_widget": next((t for t in CHAT_TOOLS if t in low), "") or ("chat" if "chat" in categories else ""),
        "review_widget": next((t for t in REVIEW_TOOLS if t in low), "") or ("reviews" if "reviews" in categories else ""),
        "form": bool(re.search(r"<form[^>]*>.*?(type=\"?email|type=\"?tel|name=\"?(your-)?email)", low, re.S))
        or "forms" in categories,
        "quote_form": bool(QUOTE_WORDS.search(text)),
        "phone_link": "href=\"tel:" in low or "href='tel:" in low,
    }


def merge_signals(a: dict | None, b: dict | None) -> dict:
    """Two looks at the same site (plain fetch + real browser): anything seen in either counts."""
    a, b = a or {}, b or {}
    return {k: a.get(k) or b.get(k) for k in set(a) | set(b)}


def industry_order(category: str) -> list[str]:
    low = (category or "").lower()
    for words, order in INDUSTRIES:
        if any(w in low for w in words):
            return order
    return DEFAULT_ORDER


def _gap(key: str, s: dict) -> str:
    """The evidence for this automation on this site ("" = nothing seen that calls for it)."""
    if key == "online_booking" and not s.get("booking_tool"):
        if s.get("booking_cta"):
            return "There are 'Book' buttons, but I couldn't find an online booking tool behind them; booking seems to go through a phone call or a form."
        return "I couldn't find a way to book online on the pages I checked; booking seems to be by phone or form."
    if key == "instant_reply" and s.get("form") and not s.get("chat_widget"):
        return "Enquiries come in through a contact form, and I couldn't see anything that answers people straight away."
    if key == "quote_followup" and s.get("quote_form"):
        return "The site asks visitors to request a quote or estimate."
    if key == "ai_assistant" and not s.get("chat_widget"):
        return "I couldn't find a chat or messaging option on the site, so questions outside office hours have to wait."
    if key == "review_requests" and not s.get("review_widget"):
        return "I couldn't find reviews from a review platform shown on the site."
    if key == "missed_call_textback" and s.get("phone_link") and not s.get("booking_tool") and not s.get("chat_widget"):
        return "The phone number is the main way to get in touch, with no online booking or chat as a backup."
    return ""


def candidates(signals: dict | None, category: str, offered: list[str] | None = None, limit: int = 3) -> list[dict]:
    """Automations worth proposing to this business, best first: [{key, name, what, evidence, basis}].

    basis "seen": the website shows a gap this automation fills; "question": nothing seen, the industry's most
    common need, to be asked as a question. Only automations in `offered` (default: all)."""
    offered_keys = [k for k in (offered if offered is not None else ALL_KEYS) if k in AUTOMATIONS]
    if not offered_keys:
        return []
    order = [k for k in industry_order(category) if k in offered_keys] + [k for k in offered_keys if k not in industry_order(category)]
    s = signals or {}
    found = []
    for key in order:
        evidence = _gap(key, s) if s else ""
        if evidence:
            name, what = AUTOMATIONS[key]
            found.append({"key": key, "name": name, "what": what, "evidence": evidence, "basis": "seen"})
        if len(found) >= limit:
            break
    if not found and order:
        key = order[0]
        name, what = AUTOMATIONS[key]
        found.append({"key": key, "name": name, "what": what, "evidence": "", "basis": "question"})
    return found


def parse_offers(value: str | None) -> list[str]:
    """The automations you offer, from the stored setting ("" = none; None = not set yet, so all)."""
    if value is None:
        return list(ALL_KEYS)
    return [k for k in (x.strip() for x in value.split(",")) if k in AUTOMATIONS]
