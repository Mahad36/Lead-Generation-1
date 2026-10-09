# Security

- Passwords hashed with bcrypt
- JWT access tokens + hashed refresh tokens
- OAuth `state` is a short-lived signed token (`type=oauth_state`) bound to the SaaS user
- Access tokens cannot be used as OAuth state
- OAuth tokens encrypted at rest; never logged
- Tenant filters on every protected resource
- Rate limiting on the API
- Unsubscribe links are signed JWTs (not raw email + user_id)
- XSS: React text nodes by default; template preview HTML is user-owned content rendered only in their session
- Secrets stay in environment variables

Open/click tracking is off by default and is never invented.
