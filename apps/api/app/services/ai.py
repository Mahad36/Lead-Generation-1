from __future__ import annotations

import json
import shutil
from contextvars import ContextVar
from pathlib import Path
from typing import Callable, Protocol

from openai import OpenAI
from sqlalchemy.orm import Session

from app.logging_config import get_logger
from app.services.ai_catalog import get_provider_preset
from app.services.integrations import AIConfig, resolve_ai_config

logger = get_logger("ai")


class AINotConfiguredError(RuntimeError):
    pass


class AIProviderError(RuntimeError):
    """The AI provider rejected or could not complete a request."""


class AICancelledError(RuntimeError):
    """The request was stopped by the user (e.g. "Stop audit")."""


# Set by long-running work (an audit) to a function that returns True once the user asked to stop it; a running
# Claude Code process is then ended instead of being left to finish.
cancel_check: ContextVar[Callable[[], bool] | None] = ContextVar("cancel_check", default=None)


class AIProvider(Protocol):
    def generate(
        self,
        prompt: str,
        *,
        system: str,
        temperature: float | None = None,
        max_tokens: int | None = None,
        json_schema: dict | None = None,
        timeout: float | None = None,
        images: list[str] | None = None,
    ) -> str: ...


class OpenAICompatibleProvider:
    def __init__(self, config: AIConfig) -> None:
        if not config.api_key:
            raise AINotConfiguredError("Add your AI API key in Settings")
        self.client = OpenAI(api_key=config.api_key, base_url=config.api_base or None)
        self.model = config.model
        self.temperature = config.temperature
        self.timeout = config.timeout_seconds
        self.provider = config.provider

    def generate(
        self,
        prompt: str,
        *,
        system: str,
        temperature: float | None = None,
        max_tokens: int | None = None,
        json_schema: dict | None = None,
        timeout: float | None = None,
        images: list[str] | None = None,
    ) -> str:
        # json_schema is enforced by Claude only; callers still parse defensively,
        # and the prompt itself asks for JSON.
        temp = self.temperature if temperature is None else temperature
        logger.info("ai_generate", provider=self.provider, model=self.model)
        try:
            response = self.client.chat.completions.create(
                model=self.model,
                temperature=temp,
                timeout=timeout or self.timeout,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": prompt},
                ],
            )
        except Exception as exc:
            raise AIProviderError(f"{self.provider} request failed: {str(exc)[:300]}") from exc
        content = response.choices[0].message.content or ""
        return content.strip()


# Current Claude models reject sampling parameters (temperature/top_p) and run
# adaptive thinking by default; these also accept server-side refusal fallbacks.
_CLAUDE_FALLBACK_MODELS = {"claude-opus-5-5", "claude-opus-5", "claude-fable-5-1", "claude-sonnet-5-5"}
_CLAUDE_EFFORT_MODELS = _CLAUDE_FALLBACK_MODELS | {"claude-fable-5", "claude-sonnet-5", "claude-opus-4-8", "claude-opus-4-7"}


class AnthropicProvider:
    def __init__(self, config: AIConfig) -> None:
        if not config.api_key:
            raise AINotConfiguredError("Add your AI API key in Settings")
        import anthropic

        self.base = (config.api_base or "https://api.anthropic.com").rstrip("/")
        self.client = anthropic.Anthropic(
            api_key=config.api_key,
            base_url=self.base,
            timeout=float(config.timeout_seconds),
            max_retries=2,
        )
        self.model = config.model
        self.timeout = config.timeout_seconds
        self.provider = config.provider

    def generate(
        self,
        prompt: str,
        *,
        system: str,
        temperature: float | None = None,
        max_tokens: int | None = None,
        json_schema: dict | None = None,
        timeout: float | None = None,
        images: list[str] | None = None,
    ) -> str:
        import anthropic

        # temperature is accepted for interface compatibility but not sent:
        # current Claude models reject sampling parameters.
        params: dict = {
            "model": self.model,
            "max_tokens": max_tokens or 4096,
            "system": system,
            "messages": [{"role": "user", "content": _content_with_images(prompt, images)}],
        }
        output_config: dict = {}
        if json_schema:
            output_config["format"] = {"type": "json_schema", "schema": json_schema}
        if self.model in _CLAUDE_EFFORT_MODELS:
            output_config["effort"] = "medium"
        if output_config:
            params["output_config"] = output_config
        client = self.client.with_options(timeout=float(timeout or self.timeout))
        use_fallbacks = self.model in _CLAUDE_FALLBACK_MODELS and self.base == "https://api.anthropic.com"
        logger.info("ai_generate", provider=self.provider, model=self.model, structured=bool(json_schema))
        try:
            if use_fallbacks:
                response = client.beta.messages.create(
                    **params,
                    betas=["server-side-fallback-2026-07-01"],
                    fallbacks="default",
                )
            else:
                response = client.messages.create(**params)
        except anthropic.AuthenticationError as exc:
            raise AIProviderError("Claude rejected the API key. Update it in AI & settings.") from exc
        except anthropic.NotFoundError as exc:
            raise AIProviderError(f"Claude model '{self.model}' was not found. Pick another model in AI & settings.") from exc
        except anthropic.RateLimitError as exc:
            raise AIProviderError("Claude rate limit reached. Try again in a minute.") from exc
        except anthropic.APIStatusError as exc:
            raise AIProviderError(f"Claude API error {exc.status_code}: {exc.message[:300]}") from exc
        except anthropic.APIConnectionError as exc:
            raise AIProviderError("Could not reach the Claude API. Check the network and try again.") from exc

        if response.stop_reason == "refusal":
            logger.warning("ai_refusal", provider=self.provider, model=self.model, request_id=response._request_id)
            raise AIProviderError("Claude declined this request.")
        if response.stop_reason == "max_tokens":
            raise AIProviderError("Claude's response was cut off (max_tokens reached).")
        text = "".join(block.text for block in response.content if block.type == "text")
        logger.info(
            "ai_generate_done",
            model=response.model,
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
        )
        return text.strip()


def _content_with_images(prompt: str, images: list[str] | None):
    if not images:
        return prompt
    import base64

    blocks: list[dict] = []
    for path in images:
        data = base64.standard_b64encode(Path(path).read_bytes()).decode("ascii")
        blocks.append({"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": data}})
    blocks.append({"type": "text", "text": f"The images above are screenshots of the website, in this order: {', '.join(Path(p).name for p in images)}.\n\n{prompt}"})
    return blocks


CLAUDE_CODE_LOGIN_HELP = (
    "Claude Code is not logged in. In the terminal run `claude setup-token`, then add the token to .env as "
    "CLAUDE_CODE_OAUTH_TOKEN=... (or run `claude` once and log in), and run the audit again."
)


def _run_stoppable(cmd: list[str], prompt: str, *, limit: float, env: dict, cwd: str):
    """Run a command with `prompt` on stdin; end it early on timeout or when `cancel_check` says stop."""
    import subprocess
    import time
    from types import SimpleNamespace

    proc = subprocess.Popen(
        cmd,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",  # Windows defaults to the ANSI code page otherwise
        errors="replace",
        env=env,
        cwd=cwd,
    )
    deadline = time.monotonic() + limit
    should_stop = cancel_check.get()
    first = True
    while True:
        try:
            stdout, stderr = proc.communicate(input=prompt if first else None, timeout=2)
            return SimpleNamespace(returncode=proc.returncode, stdout=stdout, stderr=stderr)
        except subprocess.TimeoutExpired as exc:
            first = False
            stopped = bool(should_stop and should_stop())
            if stopped or time.monotonic() > deadline:
                proc.kill()
                proc.communicate()
                if stopped:
                    raise AICancelledError("Stopped by you.") from exc
                raise


class ClaudeCodeProvider:
    """Runs the local Claude Code CLI in headless mode on the user's Claude subscription (no API key)."""

    def __init__(self, config: AIConfig) -> None:
        from app.config import get_settings

        settings = get_settings()
        self.binary = shutil.which(settings.claude_code_bin) or shutil.which(
            str(Path.home() / ".local/bin/claude")
        )
        if not self.binary:
            raise AINotConfiguredError(
                "Claude Code is not installed on this machine. Install it with "
                "`curl -fsSL https://claude.ai/install.sh | bash`, then run `claude setup-token`."
            )
        self.model = config.model if config.model and not config.model.startswith(("gpt", "gemini")) else "opus"
        self.oauth_token = settings.claude_code_oauth_token.strip()
        self.min_timeout = settings.claude_code_timeout_seconds
        self.provider = config.provider

    def generate(
        self,
        prompt: str,
        *,
        system: str,
        temperature: float | None = None,
        max_tokens: int | None = None,
        json_schema: dict | None = None,
        timeout: float | None = None,
        images: list[str] | None = None,
    ) -> str:
        import os
        import subprocess
        import tempfile
        import time

        full_prompt = prompt
        if images:
            full_prompt += (
                "\n\nScreenshots of the website, as a visitor sees it (first screen first). Open every one with the "
                "Read tool and look at it carefully before answering:\n" + "\n".join(f"- {p}" for p in images)
            )
        cmd = [
            self.binary,
            "-p",
            "--output-format", "json",
            "--append-system-prompt", system,
            "--tools", "Read" if images else "",
            "--allowedTools", "Read",
            "--permission-mode", "dontAsk",
            "--strict-mcp-config",
            "--model", self.model,
        ]
        if json_schema:
            cmd += ["--json-schema", json.dumps(json_schema)]
        # Never let an API key take precedence over the subscription login.
        env = {k: v for k, v in os.environ.items() if k not in {"ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN"}}
        if self.oauth_token:
            env["CLAUDE_CODE_OAUTH_TOKEN"] = self.oauth_token
        cwd = str(Path(images[0]).parent) if images else tempfile.gettempdir()
        limit = max(float(timeout or 0), float(self.min_timeout))
        logger.info("claude_code_generate", model=self.model, images=len(images or []), structured=bool(json_schema))
        started = time.monotonic()
        try:
            proc = _run_stoppable(cmd, full_prompt, limit=limit, env=env, cwd=cwd)
        except subprocess.TimeoutExpired as exc:
            raise AIProviderError(f"Claude Code took longer than {int(limit)} seconds. Try again.") from exc
        except OSError as exc:
            raise AIProviderError(f"Could not run Claude Code: {exc}") from exc

        data: dict = {}
        try:
            data = json.loads(proc.stdout.strip().splitlines()[-1]) if proc.stdout.strip() else {}
        except (json.JSONDecodeError, IndexError):
            data = {}
        message = str(data.get("result") or proc.stderr or proc.stdout or "").strip()
        if proc.returncode != 0 or data.get("is_error") or not data:
            lowered = message.lower()
            logger.error("claude_code_failed", returncode=proc.returncode, error=message[:400])
            if any(s in lowered for s in ("log in", "login", "/login", "not authenticated", "oauth", "invalid api key", "authentication")):
                raise AIProviderError(CLAUDE_CODE_LOGIN_HELP)
            if any(s in lowered for s in ("usage limit", "rate limit", "limit reached")):
                raise AIProviderError("Your Claude usage limit has been reached. Try again after it resets.")
            raise AIProviderError(f"Claude Code failed: {message[:300] or f'exit code {proc.returncode}'}")
        logger.info(
            "claude_code_done",
            seconds=round(time.monotonic() - started, 1),
            turns=data.get("num_turns"),
        )
        if json_schema and data.get("structured_output") is not None:
            return json.dumps(data["structured_output"])
        return message


def get_ai_provider_for_user(db: Session, user_id: str) -> AIProvider:
    config = resolve_ai_config(db, user_id)
    preset = get_provider_preset(config.provider)
    if preset and preset["api_style"] == "claude_code":
        return ClaudeCodeProvider(config)
    if preset and preset["api_style"] == "anthropic":
        return AnthropicProvider(config)
    return OpenAICompatibleProvider(config)


ANALYSIS_SYSTEM = """You are a website analyst for a legitimate B2B outreach tool.
Only use facts present in the provided crawl and analysis data.
Never invent website problems, testimonials, traffic numbers, or reviews.
If has_lead_capture is true, do not say the site is missing a contact or lead-generation form.
If evidence is missing, say so briefly.
Write a concise, professional summary (120-180 words) plus 3-6 bullet opportunities that are supported by the evidence.
"""

EMAIL_SYSTEM = """You write concise, human B2B outreach emails.
Rules:
- Do not invent facts.
- Do not claim you manually reviewed the site unless that is stated.
- Do not use exaggerated compliments.
- Only mention a website issue if it appears in the provided analysis.
- Keep the email under 140 words.
- Sound like a person, not a mass campaign.
- Return ONLY the email body in HTML paragraphs, no subject line.
"""


def enhance_analysis(db: Session, user_id: str, rule_analysis: dict, extra_context: dict) -> str:
    provider = get_ai_provider_for_user(db, user_id)
    prompt = (
        "Rule-based analysis JSON:\n"
        f"{rule_analysis}\n\n"
        "Additional context:\n"
        f"{extra_context}\n"
    )
    return provider.generate(prompt, system=ANALYSIS_SYSTEM, temperature=0.2)


def generate_personalized_email(
    db: Session,
    user_id: str,
    *,
    template_html: str,
    business: dict,
    analysis: dict,
    sender: dict,
) -> str:
    provider = get_ai_provider_for_user(db, user_id)
    issues = analysis.get("issues") or []
    issue_titles = [i.get("title") for i in issues if i.get("title")]
    prompt = f"""
Business name: {business.get("business_name")}
City: {business.get("city")}
Category: {business.get("category")}
Website: {business.get("website")}
Platform: {business.get("platform")}
Website score: {business.get("website_score")}
Observed issues: {issue_titles}
Opportunities: {analysis.get("opportunities")}
Recommended service: {analysis.get("recommended_service")}
Services found on site: {analysis.get("services")}
Business summary: {analysis.get("business_summary")}

Sender name: {sender.get("sender_name")}
Sender company: {sender.get("company_name")}
Sender services: {sender.get("services")}
Company description: {sender.get("company_description")}

User template (use as structure/tone guidance; do not invent extra claims):
{template_html}
"""
    return provider.generate(prompt, system=EMAIL_SYSTEM, temperature=0.4)
