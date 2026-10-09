from typing import Protocol

from app.config import get_settings
from app.logging_config import get_logger

logger = get_logger("email_verify")


class EmailVerifier(Protocol):
    def verify(self, email: str) -> dict: ...


class NullEmailVerifier:
    """MVP default: do not claim verification without a real provider."""

    def verify(self, email: str) -> dict:
        return {
            "email": email,
            "status": "not_checked",
            "provider": "none",
            "details": {"reason": "No verification provider configured"},
        }


class HttpEmailVerifier:
    def __init__(self, provider: str, api_key: str) -> None:
        self.provider = provider
        self.api_key = api_key

    def verify(self, email: str) -> dict:
        # Provider-specific integrations can be added here without changing callers.
        logger.info("external_verifier_not_implemented", provider=self.provider)
        return {
            "email": email,
            "status": "unknown",
            "provider": self.provider,
            "details": {"reason": f"Provider '{self.provider}' is configured but not implemented yet"},
        }


def get_email_verifier() -> EmailVerifier:
    settings = get_settings()
    if settings.email_verifier_provider and settings.email_verifier_api_key:
        return HttpEmailVerifier(settings.email_verifier_provider, settings.email_verifier_api_key)
    return NullEmailVerifier()
