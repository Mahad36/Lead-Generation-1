import pytest

from app.services.limits import LimitValidationError, validate_send_limits
from app.utils.templates import render_template, validate_template_variables


def test_valid_limits():
    daily, hourly, min_delay, max_delay = validate_send_limits(50, 20, 60, 180)
    assert daily == 50
    assert hourly == 20
    assert min_delay == 60
    assert max_delay == 180


def test_hourly_clamps_to_daily():
    daily, hourly, _, _ = validate_send_limits(10, 20, 60, 180)
    assert daily == 10
    assert hourly == 10


def test_floors_tiny_delay():
    daily, hourly, min_delay, max_delay = validate_send_limits(20, 10, 1, 5)
    assert daily == 20
    assert hourly == 10
    assert min_delay == 5
    assert max_delay == 5


def test_rejects_zero_daily():
    with pytest.raises(LimitValidationError):
        validate_send_limits(0, 10, 60, 180)


def test_allows_large_daily():
    daily, hourly, _, _ = validate_send_limits(500, 200, 60, 180)
    assert daily == 500
    assert hourly == 200


def test_template_unknown_variables():
    unknown = validate_template_variables("Hi {{business_name}}", "Hello {{not_real}}")
    assert unknown == ["not_real"]


def test_template_render():
    assert render_template("Hello {{business_name}}", {"business_name": "ABC Plumbing"}) == "Hello ABC Plumbing"
