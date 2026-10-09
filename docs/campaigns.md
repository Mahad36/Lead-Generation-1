# Campaigns

A campaign belongs to one SaaS user, one of **that user's** Gmail accounts, and one of that user's templates.

Safety:

- Daily and hourly counters on the campaign row
- Randomized delay between min/max seconds
- Database claim (`pending/approved → sending`) so two workers cannot send the same lead
- Suppression and do-not-contact checks before send
- Signed unsubscribe tokens
- Optional approval before send

When the daily cap is hit the campaign pauses with `paused_reason=daily_limit_reached` and Celery beat resumes it the next day.
