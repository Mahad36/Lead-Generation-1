# Website audit workflow

Website URL + customer email -> measure the live site -> Claude analyses it -> PDF -> Claude email -> send with PDF attached.

No third-party testing service (such as PageSpeed Insights) is used: the app loads the site itself and Claude does the analysis.

## Use it

Quickest way to try it: `docker compose up --build`, then follow *Docker (full stack)* in the README.


- **Website audits** page (`/audits`): enter business name, website URL and customer email, click **Run website audit**.
  A lead is created (or the existing lead for that website is reused) and the audit starts.
- **Lead detail** page (`/leads/{id}`): the *Website audit & outreach* card runs the same workflow for an existing lead.

Progress is shown step by step. When the audit is ready you can open/download the PDF, edit the email (To, Subject,
Body) and click **Send email with PDF**. Tick *Send automatically* to skip the review step.

## Requirements

| What | Where |
| --- | --- |
| Claude | **AI & settings** -> provider *Claude Code (your Claude subscription, no API key)*: needs the `claude` CLI logged in on the machine running the app (`claude setup-token`, then `CLAUDE_CODE_OAUTH_TOKEN=...` in `.env`). Or provider *Claude (Anthropic)* with an API key |
| Screenshots | Playwright Chromium: `playwright install --with-deps chromium` (done by the Codespaces setup). Without it the audit runs on measured data only |
| Sending account | **Email setup** -> connect Gmail or SMTP |
| Celery worker + Redis | `celery -A workers.celery_app.celery_app worker` (without Redis the API runs the audit in a background thread) |

## How it works

| Step | Code |
| --- | --- |
| Fetch website + customer data (crawl, platform, rule-based signals) | `services/website_audit.py::_collect_site_data` (reuses `crawler`, `platform_detection`, `analysis`) |
| Measure the live site: homepage timing, headers, every file it loads (size, compression, caching), HTML source | `services/performance.py` |
| Desktop + phone screenshots of the first screens | `services/screenshots.py` (Playwright) |
| Claude reviews the screenshots, HTML and measurements like a CRO / design consultant and writes the audit + scores (structured JSON) | `services/audit_ai.py::generate_audit_report` via `services/ai.py::ClaudeCodeProvider` (`claude -p`, Read-only tools) or `AnthropicProvider` |
| PDF | `services/audit_pdf.py` (reportlab, no browser needed) |
| Claude email | `services/audit_ai.py::generate_audit_email` |
| Send with attachment, unsubscribe link, suppression check, `email_messages` log | `services/website_audit.py::send_audit` + `services/gmail.py` |

The run executes in the Celery task `workers.tasks.audits.run_website_audit`. Each step updates
`website_audits.status`; failures are stored in `error_message`/`failed_step` and shown in the UI. Logs use the
`website_audit`, `performance`, `audit_ai` and `ai` loggers with `audit_id`/`lead_id`.

## API

| Method | Path | Purpose |
| --- | --- | --- |
| POST | `/api/audits/quick` | Create/reuse a lead from `{business_name, website_url, email}` and start an audit |
| POST | `/api/leads/{lead_id}/audits` | Start an audit for an existing lead |
| GET | `/api/audits?lead_id=` | List audits |
| GET | `/api/audits/{id}` | Audit detail (report, scores, email draft) |
| GET | `/api/audits/{id}/pdf` | The PDF (`?download=1` for attachment) |
| PATCH | `/api/audits/{id}` | Save email draft / recipient |
| POST | `/api/audits/{id}/send` | Send the email with the PDF attached |

## Automatic audits (poller)

**Website audits → Automatic audits**: switch on **Run automatically** (and **Send emails automatically**), set
**Max audits per day**. Every `AUTO_AUDIT_POLL_SECONDS` (default 300) the Celery beat task
`workers.tasks.audits.poll_auto_audits` picks leads that have a website and an email, have never been audited or
contacted, aren't marked do-not-contact or on the suppression list, and runs the full audit for them
(`AUTO_AUDIT_BATCH_SIZE` at a time, default 2), sending the email when auto-send is on. It needs the worker and beat
running, Claude Code (or an API key) set up, and a connected email account; the card lists anything missing.
Code: `services/auto_audits.py`.

## Email tracking: opens, replies, bounces

| Signal | How | Needs |
| --- | --- | --- |
| **Opened** | A 1×1 transparent image at `PUBLIC_URL/t/o/<signed id>.gif` is added to each email (`services/tracking.py`). Hits within 10 s of sending (mail scanners) are ignored | `PUBLIC_URL` reachable from the internet (Cloudflare Tunnel) |
| **Replied** | Every `INBOX_CHECK_SECONDS` (300) the SMTP account's INBOX is read over IMAP, read-only, with the same login. Matches `In-Reply-To`/`References` against the `Message-ID` we sent, or any email from a contacted address (`services/inbox.py`) | SMTP account (e.g. Gmail App Password). Gmail IMAP host is derived from the SMTP host |
| **Bounced** | Same check: mail from `mailer-daemon`/`postmaster` mentioning a sent Message-ID or recipient. The message is marked `bounced` and the address goes on the suppression list | Same |

Opens are approximate: Apple Mail preloads images (false opens), Outlook often blocks them (missed opens), and
**opening your own Sent copy in Gmail also counts as an open**. Replies and bounces are exact.

The same public address serves the unsubscribe page (`/t/u/<token>`, plus RFC 8058 one-click `List-Unsubscribe`
headers), so recipients can actually unsubscribe; without `PUBLIC_URL` the link points at `FRONTEND_URL`.

### Cloudflare Tunnel (Windows, domain on Cloudflare)

```powershell
winget install Cloudflare.cloudflared
cloudflared tunnel login                        # browser: pick your domain
cloudflared tunnel create leadgen
cloudflared tunnel route dns leadgen t.example.com
```

`.localdata\cloudflared\config.yml` (only `/t/` is exposed; the app itself stays private):

```yaml
tunnel: leadgen
credentials-file: C:\Users\<you>\.cloudflared\<tunnel-id>.json
ingress:
  - hostname: t.example.com
    path: ^/t/
    service: http://127.0.0.1:8000
  - service: http_status:404
```

Add `PUBLIC_URL=https://t.example.com` to `.env`. `start-windows.bat` starts the tunnel automatically when that
config file exists (log: `.localdata\logs\tunnel.log`).

## Scraped leads → personalised outreach (end to end)

Scrape (Apify Google Maps, or a CSV import) → website check (`services/website_pipeline.py`) → the automatic audit
poller is woken (`auto_audits.request_poll`) → audit → PDF → Claude email using the lead's company, contact name and
the audit's own findings → send with the PDF attached → open / click / reply / bounce tracking.

- **Switch:** *Automatic outreach* card on the Scraping and Website audits pages (on/off, auto-send, max per day).
  Scraped leads no longer go into a template campaign; campaigns are still available for manual use.
- **Isolation:** each lead is its own audit job with its own error (`website_audits.error_message` / `failed_step`), so
  one bad website or address never stops the batch. At most `AUTO_AUDIT_BATCH_SIZE` run at once, within the daily limit.
- **Retries:** a failed audit is retried after `AUTO_AUDIT_RETRY_MINUTES` (30) up to `AUTO_AUDIT_MAX_ATTEMPTS` (2) audits
  per lead; a failed automatic send is retried up to `AUTO_SEND_MAX_ATTEMPTS` (3) times. Suppressed / do-not-contact
  addresses are not retried.
- **Per-lead actions** (lead page → *Website audit & outreach*): Run new audit, Regenerate PDF
  (`POST /api/audits/{id}/regenerate-pdf`, reuses the report and the screenshots kept in `AUDIT_FILES_DIR` for 30 days),
  Regenerate email (`POST /api/audits/{id}/regenerate-email`, background), edit and Send / Send again.
- **Workflow status** (`services/lead_outreach.py`, returned as `outreach` on every lead): Scraped → Website found →
  Auditing → Website audited → PDF generated → Email generated → Email sent → Opened → Link clicked → Replied, plus
  Failed (with the step), Sending failed, Bounced, No website, No email. Derived from the lead, its latest audit and its
  email, so it can't drift out of sync. Shown in the lead tables (Leads file view, Scraping page) and as a stepper on the
  lead page.
- **Contact name:** `leads.contact_name`, from Apify when it returns a person, CSV columns `contact name` / `contact` /
  `owner` / `first name`, or edited on the lead page. Claude greets the contact by first name when it is set.
- **Click tracking:** links in sent emails go through `PUBLIC_URL/t/c/<signed token>` (no open redirect; scanner hits in
  the first 10 s are ignored). Audit emails contain no links by design, so clicks are only counted for links you add.
  Turn off with `ENABLE_EMAIL_CLICK_TRACKING=false`.
