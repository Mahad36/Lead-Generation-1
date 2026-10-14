from fastapi import APIRouter, Depends, Query

from app.deps import get_current_user
from app.models import User
from app.services import markets

router = APIRouter(prefix="/scheduling", tags=["scheduling"])


@router.get("/plan")
def sending_plan(
    country: str = Query(min_length=1, max_length=128),
    areas: list[str] = Query(default=[]),
    user: User = Depends(get_current_user),
):
    """Best time to send for these places: in their time, and the same hours on your clock, per time zone."""
    return markets.plan(country, areas)


@router.get("/cities")
def suggested_cities(
    country: str = Query(min_length=1, max_length=128),
    state: str = Query(default="", max_length=128),
    user: User = Depends(get_current_user),
):
    """Suggested places to search: biggest business markets and high-income areas (a curated guide)."""
    return markets.suggested_cities(country, state)
