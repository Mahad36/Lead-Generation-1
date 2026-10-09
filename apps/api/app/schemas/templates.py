from datetime import datetime

from pydantic import BaseModel, Field


class TemplateCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    subject: str = Field(min_length=1, max_length=500)
    html_body: str = Field(min_length=1)
    text_body: str = ""
    is_active: bool = True


class TemplateUpdate(BaseModel):
    name: str | None = None
    subject: str | None = None
    html_body: str | None = None
    text_body: str | None = None
    is_active: bool | None = None


class TemplateOut(BaseModel):
    id: str
    name: str
    subject: str
    html_body: str
    text_body: str
    is_active: bool
    created_at: datetime
    updated_at: datetime
    unknown_variables: list[str] = []

    model_config = {"from_attributes": True}


class TemplatePreviewRequest(BaseModel):
    lead_id: str | None = None
    subject: str | None = None
    html_body: str | None = None
    text_body: str | None = None


class TemplatePreviewOut(BaseModel):
    subject: str
    html_body: str
    text_body: str
    context: dict
