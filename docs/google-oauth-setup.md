# Google OAuth and Gmail API setup

This SaaS lets **each registered user** connect **their own** Gmail or Google Workspace account. Do not put a developer mailbox into the app as a global sender.

## 1. Create a Google Cloud project

1. Open [Google Cloud Console](https://console.cloud.google.com/).
2. Create a project for this product.

## 2. Enable Gmail API

APIs & Services → Library → enable **Gmail API**.

## 3. Configure the OAuth consent / Google Auth Platform

1. Audience: **External** for a public SaaS.
2. Add app name, support email, and logo.
3. Add your production authorized domain (not localhost).
4. Scopes used by this app:

   - `https://www.googleapis.com/auth/gmail.send`
   - `https://www.googleapis.com/auth/userinfo.email`
   - `https://www.googleapis.com/auth/userinfo.profile`
   - `openid`

The app does not request full mailbox read access.

## 4. Create OAuth client credentials

Create a **Web application** client.

Authorized redirect URIs:

- Local: `http://localhost:8000/api/email-accounts/gmail/callback`
- Production: `https://YOUR_API_HOST/api/email-accounts/gmail/callback`

Put values in `.env`:

```
GOOGLE_CLIENT_ID=
GOOGLE_CLIENT_SECRET=
GOOGLE_REDIRECT_URI=http://localhost:8000/api/email-accounts/gmail/callback
```

Never commit these values. Never expose them to the Next.js bundle.

## 5. Local vs production

`GOOGLE_REDIRECT_URI` must match the backend callback exactly. The frontend only receives `{ authorization_url }` and redirects the browser to Google.

## 6. Production verification

Public Gmail send access usually requires Google app verification and a security assessment if you leave testing mode. Until then, only test users listed in the consent screen can connect.

See also `docs/gmail-setup.md` and `docs/security.md`.
