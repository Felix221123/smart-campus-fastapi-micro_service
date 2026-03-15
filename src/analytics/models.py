from __future__ import annotations

import os
import uuid
from datetime import date, datetime
from typing import Optional, Dict, Any

from sqlalchemy import Column, String, Integer, Float, Boolean, DateTime, Date, text
from sqlalchemy.dialects.postgresql import UUID, JSONB

from src.module.models import Base

DB_SCHEMA = os.getenv("DB_SCHEMA", "public")


def _table_args():
    return {"schema": DB_SCHEMA}


class AssistantQueryAnalytics(Base):
    __tablename__ = "assistant_query_analytics"
    __table_args__ = _table_args()

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    conversation_id = Column(UUID(as_uuid=True), nullable=False, index=True)
    user_id = Column(UUID(as_uuid=True), nullable=True, index=True)

    channel = Column(String, nullable=False, default="web")
    question_text = Column(String, nullable=False)
    normalized_question = Column(String, nullable=True)
    question_hash = Column(String(128), nullable=True, index=True)

    detected_intent = Column(String, nullable=False, index=True)
    route_reason = Column(String, nullable=True)
    tool_name = Column(String, nullable=True, index=True)

    answer_status = Column(String, nullable=True)
    requires_user_choice = Column(Boolean, nullable=False, default=False)
    has_error = Column(Boolean, nullable=False, default=False)
    error_code = Column(String, nullable=True)

    latency_ms = Column(Integer, nullable=True)
    rag_hit_count = Column(Integer, nullable=True)
    rag_top_similarity = Column(Float, nullable=True)
    selected_option = Column(Integer, nullable=True)

    pending_action_type = Column(String, nullable=True)
    booking_step = Column(String, nullable=True)

    module_id = Column(UUID(as_uuid=True), nullable=True)
    assessment_id = Column(UUID(as_uuid=True), nullable=True)
    space_id = Column(UUID(as_uuid=True), nullable=True)

    meta_information = Column(JSONB, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=text("now()"))


class StudentRiskSnapshot(Base):
    __tablename__ = "student_risk_snapshots"
    __table_args__ = _table_args()

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    student_id = Column(UUID(as_uuid=True), nullable=False, index=True)
    snapshot_date = Column(Date, nullable=False, index=True)

    overall_risk_score = Column(Float, nullable=False, default=0)
    risk_level = Column(String, nullable=False, default="low")

    overdue_assessment_count = Column(Integer, nullable=False, default=0)
    upcoming_assessment_count = Column(Integer, nullable=False, default=0)
    average_grade = Column(Float, nullable=True)
    grade_trend_delta = Column(Float, nullable=True)

    assessment_query_count = Column(Integer, nullable=False, default=0)
    timetable_query_count = Column(Integer, nullable=False, default=0)
    no_hit_query_count = Column(Integer, nullable=False, default=0)
    unread_notification_count = Column(Integer, nullable=False, default=0)

    risk_reasons = Column(JSONB, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=text("now()"))
