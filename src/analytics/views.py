from __future__ import annotations

from datetime import date
from typing import List, Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from src.database import get_db
from src.analytics import service
from src.analytics.schemas import (
    AssessmentPressureResponse,
    FrequentQuestionItem,
    ModuleConfusionResponse,
    OverviewResponse,
    RecommendationItem,
    RiskRebuildResponse,
    SpaceDemandResponse,
    StudentRiskItem,
    TimetableFrictionResponse,
    TrendDatum,
    UnansweredQuestionItem,
)

router = APIRouter(prefix="/analytics", tags=["Analytics"])


@router.get("/overview", response_model=OverviewResponse)
def analytics_overview(days: int = Query(default=30, ge=1, le=365), db: Session = Depends(get_db)):
    return service.get_overview(db, days=days)


@router.get("/intents/trend", response_model=List[TrendDatum])
def intent_trend(
    days: int = Query(default=30, ge=1, le=365),
    bucket: str = Query(default="day"),
    db: Session = Depends(get_db),
):
    return service.get_intent_trend(db, days=days, bucket=bucket)


@router.get("/questions/frequent", response_model=List[FrequentQuestionItem])
def frequent_questions(
    days: int = Query(default=30, ge=1, le=365),
    limit: int = Query(default=10, ge=1, le=50),
    db: Session = Depends(get_db),
):
    return service.get_frequent_questions(db, days=days, limit=limit)


@router.get("/questions/unanswered", response_model=List[UnansweredQuestionItem])
def unanswered_questions(
    days: int = Query(default=30, ge=1, le=365),
    limit: int = Query(default=10, ge=1, le=50),
    db: Session = Depends(get_db),
):
    return service.get_unanswered_questions(db, days=days, limit=limit)


@router.get("/spaces/demand", response_model=SpaceDemandResponse)
def spaces_demand(
    days: int = Query(default=60, ge=1, le=365),
    limit: int = Query(default=10, ge=1, le=50),
    db: Session = Depends(get_db),
):
    return service.get_spaces_demand(db, days=days, limit=limit)


@router.get("/assessments/pressure", response_model=AssessmentPressureResponse)
def assessment_pressure(
    days_ahead: int = Query(default=30, ge=1, le=180),
    limit: int = Query(default=10, ge=1, le=50),
    db: Session = Depends(get_db),
):
    return service.get_assessment_pressure(db, days_ahead=days_ahead, limit=limit)


@router.get("/modules/confusion", response_model=ModuleConfusionResponse)
def module_confusion(
    days: int = Query(default=30, ge=1, le=365),
    limit: int = Query(default=10, ge=1, le=50),
    db: Session = Depends(get_db),
):
    return service.get_module_confusion(db, days=days, limit=limit)


@router.get("/timetable/friction", response_model=TimetableFrictionResponse)
def timetable_friction(
    days: int = Query(default=30, ge=1, le=365),
    limit: int = Query(default=10, ge=1, le=50),
    db: Session = Depends(get_db),
):
    return service.get_timetable_friction(db, days=days, limit=limit)


@router.post("/students/at-risk/rebuild", response_model=RiskRebuildResponse)
def rebuild_risk_snapshots(
    snapshot_date: Optional[date] = None,
    db: Session = Depends(get_db),
):
    return service.rebuild_student_risk_snapshots(db, snapshot_date=snapshot_date)


@router.get("/students/at-risk", response_model=List[StudentRiskItem])
def students_at_risk(
    risk_level: Optional[str] = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_db),
):
    return service.get_students_at_risk(db, risk_level=risk_level, limit=limit)


@router.get("/recommendations", response_model=List[RecommendationItem])
def analytics_recommendations(
    days: int = Query(default=30, ge=1, le=365),
    db: Session = Depends(get_db),
):
    return service.get_recommendations(db, days=days)
