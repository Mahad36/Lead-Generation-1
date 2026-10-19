import re

GENERIC_PREFIXES = (
    "info",
    "hello",
    "contact",
    "office",
    "admin",
    "support",
    "sales",
    "team",
    "enquiries",
    "inquiries",
    "mail",
)

FALSE_POSITIVE_DOMAINS = {
    "example.com",
    "email.com",
    "domain.com",
    "example.org",
    "example.net",
    "mysite.com",
    "yoursite.com",
    "yourdomain.com",
    "company.com",
    "website.com",
    "test.com",
    "sentry.io",
    "wixpress.com",
    "sentry-next.wixpress.com",
    "squareup.com",
    "schema.org",
    "w3.org",
    "googleapis.com",
    "gstatic.com",
    "cloudflare.com",
    "jquery.com",
    # website-template placeholders
    "mailservice.com",
    "yourcompany.com",
    "yourbusiness.com",
    "yourwebsite.com",
    "yoursite.net",
    "sitename.com",
    "domainname.com",
    "companyname.com",
    "emailaddress.com",
    "yourmail.com",
    "mydomain.com",
    "mywebsite.com",
    "mycompany.com",
    # site builders' own domains: their template placeholders (e.g. filler@godaddy.com), never a business inbox
    "godaddy.com",
    "secureserver.net",
    "editmysite.com",
    "squarespace.com",
    "wix.com",
    "weebly.com",
    "duda.co",
    "domain.tld",
}

FALSE_POSITIVE_LOCAL = {
    "user",
    "username",
    "email",
    "name",
    "yourname",
    "youremail",
    "your-email",
    "your.email",
    "example",
    "test",
    "johndoe",
    "john.doe",
    "janedoe",
    "jane.doe",
    "noreply",
    "no-reply",
    "donotreply",
    "mailer-daemon",
    # website-template placeholders
    "mymail",
    "myemail",
    "my.email",
    "yourmail",
    "your_email",
    "youremailaddress",
    "emailaddress",
    "email.address",
    "first.last",
    "firstname.lastname",
    "firstname",
    "name.surname",
    "someone",
    "somebody",
    "sample",
    "demo",
    "filler",
    "placeholder",
    "lorem",
    "loremipsum",
}

EMAIL_RE = re.compile(
    r"[A-Z0-9._%+\-]+@[A-Z0-9.\-]+\.[A-Z]{2,}",
    re.IGNORECASE,
)


def extract_emails_from_text(text: str) -> list[str]:
    if not text:
        return []
    found = {m.group(0).strip().lower().rstrip(".,;:>)") for m in EMAIL_RE.finditer(text)}
    return [e for e in found if is_plausible_email(e)]


def is_plausible_email(email: str) -> bool:
    value = (email or "").strip().lower()
    if value.count("@") != 1:
        return False
    local, domain = value.split("@", 1)
    if not local or not domain or "." not in domain:
        return False
    if domain in FALSE_POSITIVE_DOMAINS:
        return False
    if any(domain.endswith(f".{d}") for d in FALSE_POSITIVE_DOMAINS):
        return False
    if local in FALSE_POSITIVE_LOCAL:
        return False
    if local.endswith(".png") or local.endswith(".jpg") or local.endswith(".css"):
        return False
    if len(value) > 254:
        return False
    return True


def choose_primary_email(emails: list[str], domain: str = "") -> str | None:
    if not emails:
        return None
    unique = []
    seen = set()
    for email in emails:
        n = email.lower()
        if n not in seen:
            seen.add(n)
            unique.append(n)

    def score(email: str) -> tuple:
        local, host = email.split("@", 1)
        domain_match = 0 if domain and host.endswith(domain) else 1
        prefix_rank = 0
        for i, prefix in enumerate(GENERIC_PREFIXES):
            if local == prefix or local.startswith(f"{prefix}+"):
                prefix_rank = i
                break
        else:
            prefix_rank = 50
        return (domain_match, prefix_rank, len(email))

    return sorted(unique, key=score)[0]
