from __future__ import annotations

from datetime import date, datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class KPIItem(BaseModel):
    label: str
    value: float | int


class ChartDatum(BaseModel):
    label: str
    value: float | int


class TrendDatum(BaseModel):
    period: datetime | date | str
    series: str
    value: int


class FrequentQuestionItem(BaseModel):
    question: str
    normalized_question: Optional[str] = None
    detected_intent: Optional[str] = None
    tool_name: Optional[str] = None
    frequency: int
    latest_seen: datetime


class UnansweredQuestionItem(BaseModel):
    question: str
    normalized_question: Optional[str] = None
    detected_intent: Optional[str] = None
    tool_name: Optional[str] = None
    frequency: int
    gap_type: str
    latest_seen: datetime


class SpaceDemandItem(BaseModel):
    space_id: Optional[str] = None
    name: str
    space_type: str
    location: str
    bookings: int
    unique_users: int


class HeatmapCell(BaseModel):
    day_of_week: int
    hour_of_day: int
    value: int


class AssessmentPressureItem(BaseModel):
    module_id: Optional[str] = None
    module_code: str
    module_name: str
    upcoming_assessment_count: int
    next_due_date: Optional[datetime] = None
    average_weight: Optional[float] = None
    average_grade: Optional[float] = None


class ModuleConfusionItem(BaseModel):
    module_code: str
    query_count: int
    no_hit_count: int
    timetable_queries: int
    assessment_queries: int
    confusion_score: float


class TimetableFrictionItem(BaseModel):
    question: str
    frequency: int
    no_hit_count: int
    latest_seen: datetime


class StudentRiskItem(BaseModel):
    student_id: str
    full_name: str
    email: str
    programme: Optional[str] = None
    year_of_study: Optional[str] = None
    snapshot_date: date
    overall_risk_score: float
    risk_level: str
    overdue_assessment_count: int
    upcoming_assessment_count: int
    average_grade: Optional[float] = None
    grade_trend_delta: Optional[float] = None
    assessment_query_count: int
    timetable_query_count: int
    no_hit_query_count: int
    unread_notification_count: int
    risk_reasons: Dict[str, Any] = Field(default_factory=dict)


class RecommendationItem(BaseModel):
    title: str
    priority: str
    rationale: str
    action: str


class OverviewResponse(BaseModel):
    kpis: List[KPIItem]
    top_intents: List[ChartDatum]
    top_tools: List[ChartDatum]
    daily_usage: List[Dict[str, Any]]


class SpaceDemandResponse(BaseModel):
    top_spaces: List[SpaceDemandItem]
    space_type_breakdown: List[ChartDatum]
    booking_heatmap: List[HeatmapCell]
    library_signals: List[KPIItem]


class AssessmentPressureResponse(BaseModel):
    modules: List[AssessmentPressureItem]
    assessment_query_trend: List[Dict[str, Any]]


class ModuleConfusionResponse(BaseModel):
    modules: List[ModuleConfusionItem]


class TimetableFrictionResponse(BaseModel):
    summary: List[KPIItem]
    top_questions: List[TimetableFrictionItem]
    trend: List[Dict[str, Any]]


class RiskRebuildResponse(BaseModel):
    snapshot_date: date
    students_processed: int
    high_risk: int
    medium_risk: int
    low_risk: int
