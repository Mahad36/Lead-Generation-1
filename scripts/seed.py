"""Development seed with clearly fictional businesses."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
API = ROOT / "apps" / "api"
sys.path[:0] = [str(API), str(ROOT)]

from app.database import SessionLocal
from app.models import EmailTemplate, Lead, User, UserService, UserSettings
from app.services.leads import persist_lead
from app.utils.security import hash_password


def main() -> None:
    db = SessionLocal()
    email = "demo@leadgen.local"
    user = db.query(User).filter(User.email == email).first()
    if not user:
        user = User(email=email, hashed_password=hash_password("DemoPass123!"), full_name="Demo Owner", is_verified=True)
        db.add(user)
        db.flush()
        db.add(UserSettings(user_id=user.id, sender_name="Alex Rivera", company_name="Example Web Studio", company_website="https://example-studio.test", company_description="We help local service businesses improve their websites.", contact_email=email))
        for name in ("Website Design", "Shopify Development", "WordPress Development"):
            db.add(UserService(user_id=user.id, name=name, description=f"Sample {name} offering"))
    samples = [
        {"business_name": "ABC Plumbing", "category": "Plumbers", "country": "USA", "city": "Miami", "address": "100 Sample Ave", "primary_phone": "3055550100", "primary_email": "info@abc-plumbing.example", "website_url": "https://abc-plumbing.example"},
        {"business_name": "XYZ Dental", "category": "Dentists", "country": "Canada", "city": "Toronto", "address": "200 Example Rd", "primary_phone": "4165550101", "primary_email": "hello@xyz-dental.example", "website_url": "https://xyz-dental.example"},
        {"business_name": "Example Roofing", "category": "Roofers", "country": "USA", "city": "Dallas", "address": "300 Demo St", "primary_phone": "2145550102", "website_url": "https://example-roofing.test"},
    ]
    for row in samples:
        existing = db.query(Lead).filter(Lead.user_id == user.id, Lead.business_name == row["business_name"]).first()
        if not existing:
            persist_lead(db, user.id, row, source="seed")
    if not db.query(EmailTemplate).filter(EmailTemplate.user_id == user.id).first():
        db.add(EmailTemplate(user_id=user.id, name="Website opportunity", subject="Quick idea for {{business_name}}", html_body="<p>Hi {{business_name}},</p><p>{{personalized_message}}</p><p>{{sender_name}}</p><p><a href='{{unsubscribe_url}}'>Unsubscribe</a></p>", text_body="Hi {{business_name}}"))
    db.commit()
    _seed_test_inbox(db, user.id)
    print("Seeded demo@leadgen.local / DemoPass123!")
    db.close()


def _seed_test_inbox(db, user_id: str) -> None:
    """Connect a sending account that delivers to a local test inbox (Mailpit) when configured."""
    import os

    host = os.environ.get("TEST_INBOX_SMTP_HOST", "").strip()
    if not host:
        return
    from app.models import EmailAccount
    from app.services.gmail import store_smtp_account

    address = os.environ.get("TEST_INBOX_SENDER", "you@leadgen-test.com")
    if db.query(EmailAccount).filter(EmailAccount.user_id == user_id, EmailAccount.email_address == address).first():
        return
    try:
        store_smtp_account(
            db,
            user_id,
            email_address=address,
            host=host,
            port=int(os.environ.get("TEST_INBOX_SMTP_PORT", "1025")),
            username="test",
            password="test",
            use_tls=False,
            display_name="Test inbox (Mailpit)",
        )
        print(f"Connected test inbox sender {address} via {host}")
    except Exception as exc:  # the app still works; the user can connect an account in Email setup
        db.rollback()
        print(f"Test inbox not connected: {exc}")


if __name__ == "__main__":
    main()
