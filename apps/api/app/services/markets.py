"""Where and when to reach businesses: time zones, best sending times and suggested cities, by country.

- Time zones: a lead's local time comes from its country and the area it was found in ("Miami, Florida",
  "New South Wales"). Countries with several zones (US, Canada, Australia) are resolved by state / province.
- Best time: cold email to small businesses does best mid-week, early in the working morning, in the recipient's
  own time (they clear their inbox before the day gets busy). Countries with a different working week get theirs.
- Suggested cities: a curated guide, not live data: the largest business markets (most businesses to find) and
  high-income areas (owners who can pay for a better website), per country.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

# Python weekday numbers: Monday = 0 ... Sunday = 6
WEEKDAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]

DEFAULT_BEST = {"days": [1, 2, 3], "start": 9, "end": 11}  # Tue-Thu, 9:00-11:00 local

# Countries whose working week is not Monday-Friday
BEST_TIME_BY_COUNTRY = {
    "Saudi Arabia": {"days": [6, 0, 1], "start": 9, "end": 11, "note": "Working week is Sunday-Thursday."},
    "Kuwait": {"days": [6, 0, 1], "start": 9, "end": 11, "note": "Working week is Sunday-Thursday."},
    "Qatar": {"days": [6, 0, 1], "start": 9, "end": 11, "note": "Working week is Sunday-Thursday."},
    "Oman": {"days": [6, 0, 1], "start": 9, "end": 11, "note": "Working week is Sunday-Thursday."},
    "United Arab Emirates": {"days": [1, 2, 3], "start": 9, "end": 11, "note": "Monday-Friday, Friday is short."},
}

COUNTRY_TZ = {
    "United Kingdom": "Europe/London",
    "Ireland": "Europe/Dublin",
    "Germany": "Europe/Berlin",
    "Netherlands": "Europe/Amsterdam",
    "France": "Europe/Paris",
    "Spain": "Europe/Madrid",
    "Italy": "Europe/Rome",
    "Belgium": "Europe/Brussels",
    "Switzerland": "Europe/Zurich",
    "Austria": "Europe/Vienna",
    "Sweden": "Europe/Stockholm",
    "Norway": "Europe/Oslo",
    "Denmark": "Europe/Copenhagen",
    "Poland": "Europe/Warsaw",
    "Portugal": "Europe/Lisbon",
    "New Zealand": "Pacific/Auckland",
    "United Arab Emirates": "Asia/Dubai",
    "Saudi Arabia": "Asia/Riyadh",
    "Qatar": "Asia/Qatar",
    "Kuwait": "Asia/Kuwait",
    "Oman": "Asia/Muscat",
    "Singapore": "Asia/Singapore",
    "Malaysia": "Asia/Kuala_Lumpur",
    "India": "Asia/Kolkata",
    "Pakistan": "Asia/Karachi",
    "South Africa": "Africa/Johannesburg",
    "Japan": "Asia/Tokyo",
    "Hong Kong": "Asia/Hong_Kong",
    "Philippines": "Asia/Manila",
    "Nigeria": "Africa/Lagos",
    "Kenya": "Africa/Nairobi",
    "Egypt": "Africa/Cairo",
    "Turkey": "Europe/Istanbul",
    "Mexico": "America/Mexico_City",
    "Brazil": "America/Sao_Paulo",
    # Several zones: the most populous one when the state / province is unknown
    "United States": "America/New_York",
    "Canada": "America/Toronto",
    "Australia": "Australia/Sydney",
}

US_STATES = {
    "Alabama": ("AL", "America/Chicago"), "Alaska": ("AK", "America/Anchorage"), "Arizona": ("AZ", "America/Phoenix"),
    "Arkansas": ("AR", "America/Chicago"), "California": ("CA", "America/Los_Angeles"),
    "Colorado": ("CO", "America/Denver"), "Connecticut": ("CT", "America/New_York"),
    "Delaware": ("DE", "America/New_York"), "District of Columbia": ("DC", "America/New_York"),
    "Florida": ("FL", "America/New_York"), "Georgia": ("GA", "America/New_York"), "Hawaii": ("HI", "Pacific/Honolulu"),
    "Idaho": ("ID", "America/Boise"), "Illinois": ("IL", "America/Chicago"), "Indiana": ("IN", "America/Indiana/Indianapolis"),
    "Iowa": ("IA", "America/Chicago"), "Kansas": ("KS", "America/Chicago"), "Kentucky": ("KY", "America/New_York"),
    "Louisiana": ("LA", "America/Chicago"), "Maine": ("ME", "America/New_York"), "Maryland": ("MD", "America/New_York"),
    "Massachusetts": ("MA", "America/New_York"), "Michigan": ("MI", "America/Detroit"),
    "Minnesota": ("MN", "America/Chicago"), "Mississippi": ("MS", "America/Chicago"), "Missouri": ("MO", "America/Chicago"),
    "Montana": ("MT", "America/Denver"), "Nebraska": ("NE", "America/Chicago"), "Nevada": ("NV", "America/Los_Angeles"),
    "New Hampshire": ("NH", "America/New_York"), "New Jersey": ("NJ", "America/New_York"),
    "New Mexico": ("NM", "America/Denver"), "New York": ("NY", "America/New_York"),
    "North Carolina": ("NC", "America/New_York"), "North Dakota": ("ND", "America/Chicago"),
    "Ohio": ("OH", "America/New_York"), "Oklahoma": ("OK", "America/Chicago"), "Oregon": ("OR", "America/Los_Angeles"),
    "Pennsylvania": ("PA", "America/New_York"), "Rhode Island": ("RI", "America/New_York"),
    "South Carolina": ("SC", "America/New_York"), "South Dakota": ("SD", "America/Chicago"),
    "Tennessee": ("TN", "America/Chicago"), "Texas": ("TX", "America/Chicago"), "Utah": ("UT", "America/Denver"),
    "Vermont": ("VT", "America/New_York"), "Virginia": ("VA", "America/New_York"),
    "Washington": ("WA", "America/Los_Angeles"), "West Virginia": ("WV", "America/New_York"),
    "Wisconsin": ("WI", "America/Chicago"), "Wyoming": ("WY", "America/Denver"),
}
CA_PROVINCES = {
    "British Columbia": ("BC", "America/Vancouver"), "Alberta": ("AB", "America/Edmonton"),
    "Saskatchewan": ("SK", "America/Regina"), "Manitoba": ("MB", "America/Winnipeg"),
    "Ontario": ("ON", "America/Toronto"), "Quebec": ("QC", "America/Toronto"),
    "New Brunswick": ("NB", "America/Halifax"), "Nova Scotia": ("NS", "America/Halifax"),
    "Prince Edward Island": ("PE", "America/Halifax"), "Newfoundland and Labrador": ("NL", "America/St_Johns"),
    "Yukon": ("YT", "America/Whitehorse"), "Northwest Territories": ("NT", "America/Yellowknife"),
    "Nunavut": ("NU", "America/Iqaluit"),
}
AU_STATES = {
    "New South Wales": ("NSW", "Australia/Sydney"), "Victoria": ("VIC", "Australia/Melbourne"),
    "Queensland": ("QLD", "Australia/Brisbane"), "South Australia": ("SA", "Australia/Adelaide"),
    "Western Australia": ("WA", "Australia/Perth"), "Tasmania": ("TAS", "Australia/Hobart"),
    "Northern Territory": ("NT", "Australia/Darwin"), "Australian Capital Territory": ("ACT", "Australia/Sydney"),
}
REGIONS = {"United States": US_STATES, "Canada": CA_PROVINCES, "Australia": AU_STATES}

# A few big cities searched without their state
CITY_TZ = {
    "los angeles": "America/Los_Angeles", "san francisco": "America/Los_Angeles", "seattle": "America/Los_Angeles",
    "san diego": "America/Los_Angeles", "las vegas": "America/Los_Angeles", "portland": "America/Los_Angeles",
    "phoenix": "America/Phoenix", "denver": "America/Denver", "chicago": "America/Chicago", "houston": "America/Chicago",
    "dallas": "America/Chicago", "austin": "America/Chicago", "vancouver": "America/Vancouver",
    "calgary": "America/Edmonton", "edmonton": "America/Edmonton", "perth": "Australia/Perth",
    "brisbane": "Australia/Brisbane", "adelaide": "Australia/Adelaide", "melbourne": "Australia/Melbourne",
    "gold coast": "Australia/Brisbane",
}


def timezone_for(country: str, area: str = "") -> str:
    """IANA time zone for a place: state / province first, then known cities, then the country's main zone."""
    country = (country or "").strip()
    text = f" {area or ''} ".lower()
    for name, (code, tz) in REGIONS.get(country, {}).items():
        if f" {name.lower()}" in text or re.search(rf",\s*{code.lower()}(?:[\s,]|$)", text):
            return tz
    for city, tz in CITY_TZ.items():
        if city in text:
            return tz
    return COUNTRY_TZ.get(country, "")


def best_time(country: str) -> dict:
    best = BEST_TIME_BY_COUNTRY.get(country, DEFAULT_BEST)
    days = best["days"]
    return {
        "days": days,
        "start": best["start"],
        "end": best["end"],
        "label": f"{_days_label(days)}, {best['start']:02d}:00-{best['end']:02d}:00 their time",
        "note": best.get("note", "Mid-week mornings: owners clear their inbox before the day gets busy."),
    }


def _days_label(days: list[int]) -> str:
    ordered = sorted(days, key=lambda d: (d - 6) % 7)  # a Sunday-first week reads naturally
    if len(ordered) > 2 and all((b - a) % 7 == 1 for a, b in zip(ordered, ordered[1:])):
        return f"{WEEKDAY_NAMES[ordered[0]]}-{WEEKDAY_NAMES[ordered[-1]]}"
    return ", ".join(WEEKDAY_NAMES[d] for d in ordered)


def in_your_time(tz: str, start: int, end: int) -> str:
    """"09:00-11:00 their time" in the user's own clock, e.g. "19:00-21:00" (next working day, so DST is right)."""
    if not tz:
        return ""
    zone = ZoneInfo(tz)
    day = datetime.now(zone).replace(minute=0, second=0, microsecond=0)
    mine = datetime.now().astimezone().tzinfo
    a = day.replace(hour=start).astimezone(mine)
    b = (day.replace(hour=end % 24) + (timedelta(days=1) if end <= start else timedelta())).astimezone(mine)
    shift = ""
    if a.date() != day.date():
        shift = " (next day)" if a.date() > day.date() else " (day before)"
    return f"{a:%H:%M}-{b:%H:%M}{shift}"


def plan(country: str, areas: list[str]) -> dict:
    """Best sending time for these places, in their time and in yours, one row per time zone."""
    best = best_time(country)
    zones: dict[str, list[str]] = {}
    for area in areas or [""]:
        tz = timezone_for(country, area)
        if tz:
            zones.setdefault(tz, []).append(area or country)
    rows = []
    for tz, places in zones.items():
        now = datetime.now(ZoneInfo(tz))
        rows.append(
            {
                "timezone": tz,
                "places": places,
                "their_time_now": now.strftime("%a %H:%M"),
                "in_your_time": in_your_time(tz, best["start"], best["end"]),
            }
        )
    return {"suggestion": best, "zones": rows, "your_timezone": str(datetime.now().astimezone().tzname())}


# ---------------------------------------------------------------- suggested cities

BIG = "Most businesses"
RICH = "High-paying clients"

MARKETS: dict[str, list[tuple[str, tuple[str, ...], str]]] = {
    "United States": [
        ("New York, New York", (BIG, RICH), "Largest US business market, high spend per client"),
        ("Los Angeles, California", (BIG,), "Huge number of local service businesses"),
        ("Houston, Texas", (BIG,), "Large, fast-growing trades and home-service market"),
        ("Dallas, Texas", (BIG, RICH), "Big business base, strong incomes"),
        ("Chicago, Illinois", (BIG,), "Third-largest metro, many established firms"),
        ("Miami, Florida", (BIG, RICH), "Dense small-business market, wealthy clientele"),
        ("Phoenix, Arizona", (BIG,), "Fast-growing, lots of new businesses"),
        ("Atlanta, Georgia", (BIG,), "Major Southeast business hub"),
        ("San Diego, California", (BIG, RICH), "Affluent coastal market"),
        ("Philadelphia, Pennsylvania", (BIG,), "Large, established local economy"),
        ("Tampa, Florida", (BIG,), "Fast-growing service-business market"),
        ("Orlando, Florida", (BIG,), "Tourism and home-service businesses"),
        ("Charlotte, North Carolina", (BIG,), "Fast-growing finance hub"),
        ("Nashville, Tennessee", (BIG,), "Booming local economy"),
        ("Las Vegas, Nevada", (BIG,), "Many hospitality and service businesses"),
        ("Austin, Texas", (BIG, RICH), "Tech money, high incomes, growing fast"),
        ("Denver, Colorado", (BIG, RICH), "High incomes, active small businesses"),
        ("Seattle, Washington", (BIG, RICH), "High incomes, tech-savvy owners"),
        ("San Francisco, California", (RICH,), "Among the highest incomes in the US"),
        ("San Jose, California", (RICH,), "Silicon Valley, highest household incomes"),
        ("Boston, Massachusetts", (RICH,), "High incomes, professional services"),
        ("Washington, District of Columbia", (RICH,), "High incomes, many professional firms"),
        ("Scottsdale, Arizona", (RICH,), "Affluent, many premium service businesses"),
        ("Naples, Florida", (RICH,), "Very affluent, premium home services"),
        ("Boca Raton, Florida", (RICH,), "Affluent, premium local businesses"),
        ("Irvine, California", (RICH,), "Affluent Orange County market"),
        ("Plano, Texas", (RICH,), "Affluent Dallas suburb, many businesses"),
        ("Bellevue, Washington", (RICH,), "Affluent tech suburb of Seattle"),
        ("Raleigh, North Carolina", (RICH,), "High-income, fast-growing"),
        ("Stamford, Connecticut", (RICH,), "Affluent, close to New York"),
    ],
    "Canada": [
        ("Toronto, Ontario", (BIG, RICH), "Largest Canadian business market"),
        ("Vancouver, British Columbia", (BIG, RICH), "High incomes, many service businesses"),
        ("Montreal, Quebec", (BIG,), "Large market (much of it French-speaking)"),
        ("Calgary, Alberta", (BIG, RICH), "High incomes, strong trades market"),
        ("Edmonton, Alberta", (BIG,), "Large trades and home-service market"),
        ("Ottawa, Ontario", (BIG, RICH), "Stable, high-income market"),
        ("Mississauga, Ontario", (BIG,), "Many small businesses near Toronto"),
        ("Oakville, Ontario", (RICH,), "One of Canada's most affluent towns"),
        ("Victoria, British Columbia", (RICH,), "Affluent, many local businesses"),
    ],
    "Australia": [
        ("Sydney, New South Wales", (BIG, RICH), "Largest Australian market, high incomes"),
        ("Melbourne, Victoria", (BIG, RICH), "Huge small-business market"),
        ("Brisbane, Queensland", (BIG,), "Fast-growing, strong trades market"),
        ("Perth, Western Australia", (BIG, RICH), "High incomes, resource economy"),
        ("Gold Coast, Queensland", (BIG,), "Many tourism and service businesses"),
        ("Adelaide, South Australia", (BIG,), "Established local businesses"),
        ("Canberra, Australian Capital Territory", (RICH,), "Highest average incomes in Australia"),
        ("Sunshine Coast, Queensland", (RICH,), "Affluent, growing fast"),
    ],
    "United Kingdom": [
        ("London", (BIG, RICH), "Largest UK market by far, highest spend"),
        ("Manchester", (BIG,), "Biggest business hub outside London"),
        ("Birmingham", (BIG,), "Large, varied local economy"),
        ("Leeds", (BIG,), "Major northern business centre"),
        ("Bristol", (BIG, RICH), "Prosperous, many independents"),
        ("Edinburgh", (BIG, RICH), "Affluent Scottish capital"),
        ("Glasgow", (BIG,), "Large Scottish market"),
        ("Cambridge", (RICH,), "High incomes, many professional firms"),
        ("Oxford", (RICH,), "Affluent, many independents"),
        ("Reading", (RICH,), "Prosperous Thames Valley business hub"),
        ("Guildford", (RICH,), "Affluent Surrey town"),
        ("St Albans", (RICH,), "Affluent commuter town near London"),
    ],
    "Ireland": [
        ("Dublin", (BIG, RICH), "Most of Ireland's businesses and highest incomes"),
        ("Cork", (BIG,), "Second-largest market"),
        ("Galway", (BIG,), "Growing west-coast hub"),
        ("Limerick", (BIG,), "Regional business centre"),
    ],
    "New Zealand": [
        ("Auckland", (BIG, RICH), "Largest NZ market"),
        ("Wellington", (BIG, RICH), "High-income capital"),
        ("Christchurch", (BIG,), "Large South Island market"),
        ("Queenstown", (RICH,), "Affluent tourism businesses"),
    ],
    "Germany": [
        ("Berlin", (BIG,), "Largest city, many small businesses"),
        ("Munich", (BIG, RICH), "Highest incomes in Germany"),
        ("Hamburg", (BIG, RICH), "Wealthy port city"),
        ("Frankfurt", (BIG, RICH), "Finance hub, high incomes"),
        ("Stuttgart", (RICH,), "Affluent industrial region"),
        ("Düsseldorf", (RICH,), "Affluent business city"),
        ("Cologne", (BIG,), "Large, varied market"),
    ],
    "United Arab Emirates": [
        ("Dubai", (BIG, RICH), "Very high business density, premium budgets"),
        ("Abu Dhabi", (BIG, RICH), "High incomes, many established firms"),
        ("Sharjah", (BIG,), "Many small businesses"),
    ],
    "Saudi Arabia": [
        ("Riyadh", (BIG, RICH), "Largest Saudi market"),
        ("Jeddah", (BIG, RICH), "Major commercial city"),
        ("Dammam", (BIG,), "Eastern Province business hub"),
    ],
    "Netherlands": [
        ("Amsterdam", (BIG, RICH), "Largest Dutch market"),
        ("Rotterdam", (BIG,), "Major port city"),
        ("The Hague", (BIG, RICH), "Government and professional services"),
        ("Utrecht", (BIG, RICH), "Prosperous, central"),
    ],
    "Singapore": [("Singapore", (BIG, RICH), "Very high business density and incomes")],
}


_WORLD: dict | None = None


def _world() -> dict:
    """The largest cities of every country (population), from GeoNames (geonames.org, CC BY 4.0)."""
    global _WORLD
    if _WORLD is None:
        import json
        from pathlib import Path

        path = Path(__file__).resolve().parent.parent / "data" / "world_cities.json"
        try:
            _WORLD = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            _WORLD = {}
    return _WORLD


def _population(n: int) -> str:
    return f"{n / 1_000_000:.1f} million people" if n >= 1_000_000 else f"{n // 1000}k people"


def suggested_cities(country: str, state: str = "") -> list[dict]:
    """Suggested places for any country: the hand-picked markets (with income notes) where available, plus the
    country's largest cities by population. Only that state's / region's places when one is chosen."""
    rows = [{"area": area, "tags": list(tags), "why": why} for area, tags, why in MARKETS.get(country, [])]
    known = {r["area"].split(",")[0].lower() for r in rows}
    world = (_world().get(country) or {}).get("cities") or []
    for index, city in enumerate(world):
        name = city["area"].split(",")[0]
        if name.lower() in known:
            continue
        known.add(name.lower())
        tags = [BIG] if index < 8 else []
        why = ("Capital city, " if city.get("capital") else "") + f"about {_population(city['population'])}"
        if city.get("capital"):
            tags.append("Capital")
        rows.append({"area": city["area"], "tags": tags, "why": why})
    if state:
        in_state = [r for r in rows if state.lower() in r["area"].lower()]
        rows = in_state or rows
    return rows[:30]
