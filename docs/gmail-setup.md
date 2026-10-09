# Per-user Gmail sending

1. A signed-in SaaS user opens **Email Accounts**.
2. They click **Connect Gmail**.
3. The API builds a Google authorization URL with a signed CSRF `state` bound to that user id.
4. The user picks **their** Google account and grants send permission.
5. Google redirects to the backend callback.
6. The backend exchanges the code, encrypts tokens with `ENCRYPTION_KEY`, stores them on `email_accounts.user_id`, and reads the Gmail address via userinfo.
7. Campaigns must select an `email_account_id` that belongs to the same user.

Disconnect wipes stored credentials. Refresh re-uses the stored refresh token for that account only.

The application never asks for a Gmail password and never uses one global inbox.
