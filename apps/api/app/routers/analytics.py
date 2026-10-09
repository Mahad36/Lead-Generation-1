from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import get_current_user
from app.models import User
from app.services.analytics import dashboard_stats, timeseries

router = APIRouter(prefix="/analytics", tags=["analytics"])
dashboard_router = APIRouter(tags=["analytics"])


@router.get("/dashboard")
@dashboard_router.get("/dashboard")
def dashboard(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return dashboard_stats(db, user.id)


@router.get("/charts")
def charts(days: int = 30, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return timeseries(db, user.id, days)
