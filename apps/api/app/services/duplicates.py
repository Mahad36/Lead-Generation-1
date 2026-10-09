from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.models import Lead
from app.utils.normalize import (
    extract_domain,
    normalize_address,
    normalize_business_name,
    normalize_email,
    normalize_phone,
)


def find_duplicate_lead(
    db: Session,
    user_id: str,
    *,
    google_place_id: str = "",
    website_url: str = "",
    phone: str = "",
    business_name: str = "",
    address: str = "",
    email: str = "",
) -> Lead | None:
    if google_place_id:
        match = (
            db.query(Lead)
            .filter(Lead.user_id == user_id, Lead.google_place_id == google_place_id)
            .first()
        )
        if match:
            return match

    domain = extract_domain(website_url)
    if domain:
        match = (
            db.query(Lead)
            .filter(Lead.user_id == user_id, Lead.website_domain == domain)
            .first()
        )
        if match:
            return match

    normalized_phone = normalize_phone(phone)
    if normalized_phone and len(normalized_phone) >= 7:
        match = (
            db.query(Lead)
            .filter(Lead.user_id == user_id, Lead.primary_phone == normalized_phone)
            .first()
        )
        if match:
            return match

    normalized_email = normalize_email(email)
    if normalized_email:
        match = (
            db.query(Lead)
            .filter(Lead.user_id == user_id, Lead.primary_email == normalized_email)
            .first()
        )
        if match:
            return match

    name = normalize_business_name(business_name)
    addr = normalize_address(address)
    if name and addr:
        match = (
            db.query(Lead)
            .filter(
                Lead.user_id == user_id,
                Lead.normalized_name == name,
                Lead.normalized_address == addr,
            )
            .first()
        )
        if match:
            return match

    if name and not addr:
        match = (
            db.query(Lead)
            .filter(
                Lead.user_id == user_id,
                Lead.normalized_name == name,
                or_(Lead.city != "", Lead.country != ""),
            )
            .first()
        )
        if match and not match.address:
            return match

    return None
