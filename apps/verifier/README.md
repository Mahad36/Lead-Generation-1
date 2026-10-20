# Your own mailbox checker

Checks whether a mailbox exists before the app audits a lead and emails it, without sending anything. It runs on a
small rented server, because home internet lines can't talk to other companies' mail servers (port 25 is blocked or
throttled).

What it can and can't check:

| Leads' email host | Result |
|---|---|
| Microsoft 365, hosting companies, own mail servers | Usually a real answer: exists / doesn't exist |
| Google Workspace (business domains on Google) | "Accepts every address": Google says OK to everything, so it can't be confirmed; the email is sent as before |
| A server that blocks checkers, or says "try later" | "Unknown"; the email is sent as before |

The app only stops an email when the answer is "doesn't exist".

## What you need (about $5 a month + about $10 a year)

1. **A small Linux server that allows outbound port 25.** For example Hetzner (CX22, about €4/month), OVH or
   Contabo. Most big clouds (AWS, Google Cloud, Azure, DigitalOcean) block port 25. Hetzner unblocks it for
   new accounts on request once the first invoice is paid. Check your provider's current policy before buying.
2. **A separate, cheap domain just for checking**, e.g. `bee-check.com`. Never use beecreative.pk: if a mail
   server ever blocks the checker, your sending domain stays untouched.

## Setup (Ubuntu 24.04, about 20 minutes)

1. **DNS for the checking domain** (at the domain registrar):
   - `A` record: `verify` -> your server's IP address
   - `TXT` record on the domain itself: `v=spf1 ip4:YOUR.SERVER.IP -all`
2. **Reverse DNS** (in the server provider's panel): set the server's IP to `verify.bee-check.com`.
   Many mail servers refuse to talk to an IP without it.
3. **Install** (as root on the server). Copy this folder to the server first, e.g. `scp -r apps/verifier root@IP:/root/`:

   ```bash
   apt update && apt install -y python3-venv caddy
   useradd --system --home /opt/mailbox-checker checker
   mkdir -p /opt/mailbox-checker && cp /root/verifier/* /opt/mailbox-checker/
   cd /opt/mailbox-checker
   python3 -m venv venv && venv/bin/pip install -r requirements.txt
   cat > .env <<EOF
   VERIFIER_TOKEN=$(openssl rand -hex 24)
   HELO_NAME=verify.bee-check.com
   MAIL_FROM=check@bee-check.com
   EOF
   chown -R checker /opt/mailbox-checker
   cp mailbox-checker.service /etc/systemd/system/
   systemctl enable --now mailbox-checker
   ```

4. **HTTPS**: put your address in `Caddyfile`, then:

   ```bash
   cp Caddyfile /etc/caddy/Caddyfile && systemctl reload caddy
   ```

5. **Your token**: `grep VERIFIER_TOKEN /opt/mailbox-checker/.env`. Keep it private.

6. **Port 25 check**: `nc -vz gmail-smtp-in.l.google.com 25` should say "succeeded". If it hangs, ask the
   provider to unblock outbound port 25.

## Connect it to the app

AI & settings -> Sending -> Email verification:

- Service: **My own checker**
- Checker address: `https://verify.bee-check.com`
- Token: the token from step 5
- **Test key**: checks the token and that port 25 is open (nothing is verified or sent)
- Scope: **Every lead** is fine (no monthly allowance)

## Being a good citizen (so the checker isn't blocked)

- One check at a time per mail server, at least 8 seconds apart (`HOST_GAP_SECONDS`)
- Each address is checked once (the app stores the result; the checker also caches it for 14 days)
- A mail server that blocks the checker is skipped for a day
- At 100-300 leads a day this is light traffic. Don't use it for bulk list cleaning of thousands at once
