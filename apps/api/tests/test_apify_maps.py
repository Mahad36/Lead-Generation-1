import pytest
import respx

from app.services.apify_maps import apify_item_to_business


def test_apify_item_maps_place_fields():
    business = apify_item_to_business(
        {
            "title": "Kim's Island",
            "categoryName": "Chinese restaurant",
            "address": "175 Main St, Staten Island, NY 10307",
            "phoneUnformatted": "+17183565168",
            "website": "http://kimsislandsi.com/",
            "url": "https://www.google.com/maps/place/Kims+Island",
            "placeId": "ChIJJQz5EZzKw4kRCZ95UajbyGw",
            "totalScore": 4.5,
            "reviewsCount": 91,
        },
        "Restaurants",
    )
    assert business is not None
    assert business.business_name == "Kim's Island"
    assert business.phone == "+17183565168"
    assert business.website == "http://kimsislandsi.com/"
    assert business.google_place_id.startswith("ChIJ")
    assert business.rating == 4.5


def test_apify_item_skips_empty_title():
    assert apify_item_to_business({"website": "https://example.com"}, "Gyms") is None


def test_apify_item_maps_contact_email():
    business = apify_item_to_business(
        {"title": "Ace Plumbing", "emails": [{"email": "jobs@aceplumbing.test"}]},
        "Plumbers",
    )
    assert business is not None
    assert business.extra["email"] == "jobs@aceplumbing.test"


@respx.mock
def test_scrape_uses_given_token_and_saves_places(monkeypatch):
    import httpx

    from app.services import apify_maps

    monkeypatch.setattr(apify_maps, "time", type("T", (), {"time": staticmethod(lambda: 0), "sleep": staticmethod(lambda s: None)}))
    start = respx.post(f"{apify_maps.APIFY_BASE}/acts/{apify_maps.DEFAULT_ACTOR}/runs").mock(
        return_value=httpx.Response(201, json={"data": {"id": "run1", "defaultDatasetId": "ds1"}})
    )
    respx.get(f"{apify_maps.APIFY_BASE}/actor-runs/run1").mock(
        return_value=httpx.Response(200, json={"data": {"status": "SUCCEEDED", "defaultDatasetId": "ds1"}})
    )
    respx.get(f"{apify_maps.APIFY_BASE}/datasets/ds1/items").mock(
        return_value=httpx.Response(
            200,
            json=[
                {"title": "Sunrise Bakery", "website": "https://sunrise.example", "emails": ["hi@sunrise.example"]},
                {"title": ""},
            ],
        )
    )
    saved = []
    result = apify_maps.scrape_google_maps_apify(
        country="UK", city="Leeds", category="Bakery", max_results=5, save=saved.append, token="user-token"
    )
    assert start.calls[0].request.headers["Authorization"] == "Bearer user-token"
    assert [b.business_name for b in saved] == ["Sunrise Bakery"]
    assert saved[0].extra["email"] == "hi@sunrise.example"
    assert result["saved"] == 1 and result["errors"] == 1


def test_scrape_without_token_explains_where_to_add_it(monkeypatch):
    from app.services import apify_maps

    monkeypatch.setattr(apify_maps, "apify_token", lambda: "")
    with pytest.raises(apify_maps.ApifyError, match="AI & settings"):
        apify_maps.scrape_google_maps_apify(
            country="UK", city="Leeds", category="Bakery", max_results=5, save=lambda b: None
        )
