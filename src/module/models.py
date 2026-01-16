# models.py
# SQLAlchemy ORM models + Pydantic schemas matching your Express/TypeORM entities.

import os
import uuid
from enum import Enum as PyEnum
from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel
from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    Column,
    String,
    Float,
    Integer,
    DateTime,
    Text,
    Boolean,
    ForeignKey,
    Enum as SAEnum,
)
from sqlalchemy.dialects.postgresql import UUID, ARRAY
from sqlalchemy.orm import relationship, declarative_base
from sqlalchemy.sql import func
from sqlalchemy import JSON
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy import text

Base = declarative_base()

# -----------------------------
# Config (schema + embeddings)
# -----------------------------
DB_SCHEMA = os.getenv("DB_SCHEMA", "public")
EMBEDDING_DIM = int(os.getenv("EMBEDDING_DIM", "1536"))  # change if you use a different embedding model


def _table_args():
    return {"schema": DB_SCHEMA}


# -----------------------------
# Mixins
# -----------------------------
class UUIDPrimaryKeyMixin:
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)


class CreatedAtMixin:
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)


# -----------------------------
# Core / minimal User (for FKs)
# keeps parity with TypeORM relations.
# -----------------------------
class User(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "users"
    __table_args__ = _table_args()

    # minimal fields (extend as needed)
    email = Column(String, unique=True, nullable=True, index=True)


# -----------------------------
# Academic Service
# -----------------------------
class Module(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "modules"
    __table_args__ = _table_args()

    code = Column(String, nullable=False)
    name = Column(String, nullable=False)
    credits = Column(Integer, nullable=False)
    description = Column(Text, nullable=False)

    assessments = relationship("Assessment", back_populates="module")
    timetable_entries = relationship("TimetableEntry", back_populates="module")


class Assessment(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "assessments"
    __table_args__ = _table_args()

    module_id = Column(
        UUID(as_uuid=True),
        ForeignKey(f"{DB_SCHEMA}.modules.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    title = Column(String, nullable=False)
    description = Column(Text, nullable=False)
    due_date = Column(DateTime(timezone=True), nullable=False)
    weight = Column(Float, nullable=False)

    module = relationship("Module", back_populates="assessments")
    grades = relationship("Grade", back_populates="assessment")


class Grade(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "grades"
    __table_args__ = _table_args()

    assessment_id = Column(
        "assessmentId",
        UUID(as_uuid=True),
        ForeignKey(f"{DB_SCHEMA}.assessments.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    student_id = Column(
        "studentId",
        UUID(as_uuid=True),
        ForeignKey(f"{DB_SCHEMA}.users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    mark = Column(Float, nullable=False)
    feedback = Column(Text, nullable=True)

    graded_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    assessment = relationship("Assessment", back_populates="grades")
    student = relationship("User")


class TimetableEntry(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "timetable_entries"
    __table_args__ = _table_args()

    module_id = Column("moduleId", UUID(as_uuid=True), ForeignKey(f"{DB_SCHEMA}.modules.id", ondelete="CASCADE"), nullable=False)

    room = Column(String, nullable=False)
    start_time = Column("start_time", DateTime(timezone=True), nullable=False)
    end_time = Column("end_time", DateTime(timezone=True), nullable=False)
    session_type = Column("session_type", String, nullable=False)
    day_of_week = Column("day_of_week", String, nullable=False)

    module = relationship("Module", back_populates="timetable_entries")


# -----------------------------
# Societies / Events Service
# -----------------------------
class Society(Base, UUIDPrimaryKeyMixin, CreatedAtMixin):
    __tablename__ = "societies"
    __table_args__ = _table_args()

    name = Column(String, nullable=False)
    description = Column(Text, nullable=False)
    category = Column(String, nullable=False)

    events = relationship("Event", back_populates="organiser_society")


class Event(Base, UUIDPrimaryKeyMixin, CreatedAtMixin):
    __tablename__ = "events"
    __table_args__ = _table_args()

    title = Column(String, nullable=False)
    description = Column(Text, nullable=False)
    start_time = Column(DateTime(timezone=True), nullable=False)
    end_time = Column(DateTime(timezone=True), nullable=False)
    location = Column(String, nullable=False)
    mode = Column(String, nullable=False, default="Campus")  # Campus or Online

    organiser_society_id = Column(
        "organiserSocietyId",
        UUID(as_uuid=True),
        ForeignKey(f"{DB_SCHEMA}.societies.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    organiser_society = relationship("Society", back_populates="events")


# -----------------------------
# Space Finder / Booking Service
# -----------------------------
class Space(Base, UUIDPrimaryKeyMixin, CreatedAtMixin):
    __tablename__ = "spaces"
    __table_args__ = _table_args()

    name = Column(String, nullable=False)
    type = Column(String, nullable=False)  # Pod, StudyRoom, LibraryRoom
    location = Column(String, nullable=False)

    quiet_score = Column(Float, nullable=True)
    is_accessible = Column(Boolean, nullable=False, default=False)
    capacity = Column(Integer, nullable=True)

    bookings = relationship("SpaceBooking", back_populates="space")


class BookingStatus(str, PyEnum):
    PENDING = "PENDING"
    CONFIRMED = "CONFIRMED"
    CANCELLED = "CANCELLED"


class SpaceBooking(Base, UUIDPrimaryKeyMixin, CreatedAtMixin):
    __tablename__ = "space_bookings"
    __table_args__ = _table_args()

    space_id = Column(
        "spaceId",
        UUID(as_uuid=True),
        ForeignKey(f"{DB_SCHEMA}.spaces.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    user_id = Column(
        "userId",
        UUID(as_uuid=True),
        ForeignKey(f"{DB_SCHEMA}.users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    start_time = Column(DateTime(timezone=True), nullable=False)
    end_time = Column(DateTime(timezone=True), nullable=False)
    status = Column(
        SAEnum(BookingStatus, name="booking_status"),
        nullable=False,
        default=BookingStatus.PENDING,
    )

    space = relationship("Space", back_populates="bookings")
    user = relationship("User")


# -----------------------------
# Housing Service
# -----------------------------
class HousingListing(Base, UUIDPrimaryKeyMixin, CreatedAtMixin):
    __tablename__ = "housing_listings"
    __table_args__ = _table_args()

    owner_id = Column(
        "ownerId",
        UUID(as_uuid=True),
        ForeignKey(f"{DB_SCHEMA}.users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    title = Column(String, nullable=False)
    description = Column(Text, nullable=False)
    monthly_rent = Column(Float, nullable=False)
    location = Column(String, nullable=False)

    image_urls = Column(ARRAY(Text), nullable=True)

    bedrooms = Column(Integer, nullable=True)
    bathrooms = Column(Integer, nullable=True)
    is_furnished = Column(Boolean, nullable=False, default=False)
    is_accessible = Column(Boolean, nullable=False, default=False)
    available_from = Column(DateTime(timezone=True), nullable=False)

    owner = relationship("User")


# -----------------------------
# Notifications Service
# -----------------------------
class Notification(Base, UUIDPrimaryKeyMixin, CreatedAtMixin):
    __tablename__ = "notifications"
    __table_args__ = _table_args()

    user_id = Column(
        UUID(as_uuid=True),
        ForeignKey(f"{DB_SCHEMA}.users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    title = Column(String, nullable=False)
    body = Column(Text, nullable=False)
    type = Column(String, nullable=False)  # Assessment, System, Reminder, etc.

    scheduled_for = Column(DateTime(timezone=True), nullable=True)
    delivered_at = Column(DateTime(timezone=True), nullable=True)
    is_read = Column(Boolean, nullable=False, default=False)

    user = relationship("User")


# -----------------------------
# Library Service
# -----------------------------
class LibraryResource(Base, UUIDPrimaryKeyMixin, CreatedAtMixin):
    __tablename__ = "library_resources"
    __table_args__ = _table_args()

    title = Column(String, nullable=False)
    author = Column(String, nullable=False)
    category = Column(String, nullable=False)
    resource_type = Column(String, nullable=False)  # book, journal, ebook, etc.
    location = Column(String, nullable=True)
    is_available = Column(Boolean, nullable=False, default=True)


# -----------------------------
# Lost & Found Service
# -----------------------------
class LostFoundItem(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "lost_found"
    __table_args__ = _table_args()

    title = Column(String, nullable=False)
    description = Column(String, nullable=False)
    location = Column(String, nullable=False)
    status = Column(String, nullable=False)  # 'lost' | 'found' | 'returned'

    reported_by = Column(
        UUID(as_uuid=True),
        ForeignKey(f"{DB_SCHEMA}.users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    image_url = Column(String, nullable=True)
    reported_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    reporter = relationship("User")


# -----------------------------
# Knowledge / RAG Service (Documents, Chunks, Embeddings)
# -----------------------------
class Document(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "documents"
    __table_args__ = _table_args()

    title = Column(String, nullable=False)
    source_text = Column("sourceText", Text, nullable=False)
    uploaded_at = Column("uploadedAt", DateTime(timezone=True), nullable=True)
    uri = Column(String, nullable=False)

    chunks = relationship(
        "KnowledgeChunk",
        back_populates="document",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class KnowledgeChunk(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "knowledge_chunks"
    __table_args__ = _table_args()

    document_id = Column(UUID(as_uuid=True), ForeignKey(f"{DB_SCHEMA}.documents.id", ondelete="CASCADE"), nullable=False)
    text = Column(Text, nullable=False)

    document = relationship("Document", back_populates="chunks")
    embeddings = relationship("VectorEmbedding", back_populates="chunk", cascade="all, delete-orphan")



class VectorEmbedding(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "vector_embeddings"
    __table_args__ = _table_args()

    chunk_id = Column(UUID(as_uuid=True), ForeignKey(f"{DB_SCHEMA}.knowledge_chunks.id", ondelete="CASCADE"), nullable=False, index=True)
    text_embedding = Column(Text, nullable=True)  
    embedding = Column(Vector(EMBEDDING_DIM), nullable=True)  # new pgvector column

    chunk = relationship("KnowledgeChunk", back_populates="embeddings")



class AssistantConversation(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "assistant_conversations"
    __table_args__ = _table_args()

    user_id = Column(
        "user_id",
        UUID(as_uuid=True),
        ForeignKey(f"{DB_SCHEMA}.users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    title = Column(String, nullable=True)
    channel = Column(String, nullable=False, default="web")  # web | mobile | admin
    meta = Column("metadata", JSONB, nullable=False, server_default=text("'{}'::jsonb"))


    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    user = relationship("User")
    messages = relationship(
        "AssistantMessage",
        back_populates="conversation",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    events = relationship(
        "AssistantAgentEvent",
        back_populates="conversation",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class AssistantAgentEvent(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "assistant_agent_events"
    __table_args__ = _table_args()

    conversation_id = Column(
        "conversation_id",
        UUID(as_uuid=True),
        ForeignKey(f"{DB_SCHEMA}.assistant_conversations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    user_id = Column(
        "user_id",
        UUID(as_uuid=True),
        ForeignKey(f"{DB_SCHEMA}.users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    event_type = Column(String, nullable=False)  # intent_detected | rag_retrieval | db_query | llm_call | etc
    payload = Column(JSON, nullable=False, default=dict)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    conversation = relationship("AssistantConversation", back_populates="events")
    user = relationship("User")



class AssistantMessage(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "assistant_messages"
    __table_args__ = _table_args()

    conversation_id = Column(
        UUID(as_uuid=True),
        ForeignKey(f"{DB_SCHEMA}.assistant_conversations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    role = Column(String, nullable=False)  
    content = Column(Text, nullable=False)

    tool_name = Column(String, nullable=True)
    tool_payload = Column(JSONB, nullable=True)

    tokens_in = Column(Integer, nullable=True)
    tokens_out = Column(Integer, nullable=True)
    latency_ms = Column(Integer, nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    conversation = relationship("AssistantConversation", back_populates="messages")
