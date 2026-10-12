import re
from typing import Any

VARIABLE_RE = re.compile(r"\{\{\s*([a-zA-Z0-9_]+)\s*\}\}")


def find_variables(text: str) -> list[str]:
    return sorted(set(VARIABLE_RE.findall(text or "")))


def render_template(text: str, context: dict[str, Any]) -> str:
    def replacer(match: re.Match[str]) -> str:
        key = match.group(1)
        value = context.get(key)
        if value is None:
            return ""
        return str(value)

    return VARIABLE_RE.sub(replacer, text or "")


def validate_template_variables(subject: str, html_body: str, extra: str = "") -> list[str]:
    allowed = {
        "business_name",
        "first_name",
        "city",
        "country",
        "category",
        "website",
        "platform",
        "services",
        "website_score",
        "lead_score",
        "website_analysis",
        "website_issue",
        "recommended_service",
        "personalized_message",
        "sender_name",
        "sender_company",
        "sender_website",
        "signature",
        "unsubscribe_url",
    }
    used = set(find_variables(subject) + find_variables(html_body) + find_variables(extra))
    return sorted(used - allowed)
