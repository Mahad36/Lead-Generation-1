class LimitValidationError(ValueError):
    pass


def validate_send_limits(
    daily: int,
    hourly: int,
    min_delay: int,
    max_delay: int,
) -> tuple[int, int, int, int]:
    if daily < 1:
        raise LimitValidationError("Emails per day must be at least 1")
    hourly = daily if hourly < 1 else min(hourly, daily)
    # Floor delay so Gmail/SMTP are not hammered; the user still chooses emails/day.
    min_delay = max(int(min_delay or 15), 5)
    max_delay = max(int(max_delay or min_delay), min_delay)
    if max_delay > 3600:
        max_delay = 3600
    return daily, hourly, min_delay, max_delay
