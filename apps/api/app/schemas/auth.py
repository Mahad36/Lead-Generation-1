from typing import Annotated

from pydantic import AfterValidator, BaseModel, Field


def _app_email(value: str) -> str:
    email = value.strip().lower()
    local, sep, domain = email.partition("@")
    if not sep or not local or "." not in domain or domain.startswith(".") or domain.endswith("."):
        raise ValueError("Enter a valid email address")
    return email


AppEmail = Annotated[str, AfterValidator(_app_email)]


class RegisterRequest(BaseModel):
    email: AppEmail
    password: str = Field(min_length=8, max_length=128)
    full_name: str = Field(default="", max_length=255)


class LoginRequest(BaseModel):
    email: AppEmail
    password: str


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class RefreshRequest(BaseModel):
    refresh_token: str


class ForgotPasswordRequest(BaseModel):
    email: AppEmail


class ResetPasswordRequest(BaseModel):
    token: str
    password: str = Field(min_length=8, max_length=128)


class UserOut(BaseModel):
    id: str
    email: AppEmail
    full_name: str
    is_active: bool
    is_verified: bool
    role: str

    model_config = {"from_attributes": True}


class MessageResponse(BaseModel):
    message: str
