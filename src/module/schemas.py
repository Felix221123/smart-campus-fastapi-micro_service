# Define your module's schemas here

import uuid
from datetime import datetime
from typing import Any, Dict, Optional, List

from pydantic import BaseModel, Field
from sqlalchemy.dialects.postgresql import UUID, ARRAY

from src.module.models import BookingStatus





# Pydantic Schemas (Create/Read)

try:
    from pydantic import ConfigDict  # pydantic v2

    class ORMBase(BaseModel):
        model_config = ConfigDict(from_attributes=True)

except Exception:  # pydantic v1 fallback
    class ORMBase(BaseModel):
        class Config:
            orm_mode = True


# Modules 
class ModuleCreate(ORMBase):
    code: str
    name: str
    credits: int
    description: str


class ModuleRead(ModuleCreate):
    id: uuid.UUID
    created_at: datetime


# Assessments 
class AssessmentCreate(ORMBase):
    module_id: uuid.UUID
    title: str
    description: str
    due_date: datetime
    weight: float


class AssessmentRead(AssessmentCreate):
    id: uuid.UUID
    created_at: datetime


# Grades 
class GradeCreate(ORMBase):
    assessment_id: uuid.UUID
    student_id: uuid.UUID
    mark: float
    feedback: Optional[str] = None


class GradeRead(GradeCreate):
    id: uuid.UUID
    graded_at: datetime


# Timetable 
class TimetableEntryCreate(ORMBase):
    module_id: uuid.UUID
    room: str
    start_time: datetime
    end_time: datetime
    session_type: str
    day_of_week: str


class TimetableEntryRead(TimetableEntryCreate):
    id: uuid.UUID
    created_at: datetime


# Societies / Events 
class SocietyCreate(ORMBase):
    name: str
    description: str
    category: str


class SocietyRead(SocietyCreate):
    id: uuid.UUID
    created_at: datetime


class EventCreate(ORMBase):
    title: str
    description: str
    start_time: datetime
    end_time: datetime
    location: str
    mode: str = "Campus"
    organiser_society_id: Optional[uuid.UUID] = None


class EventRead(EventCreate):
    id: uuid.UUID
    created_at: datetime


# Spaces / Bookings 
class SpaceCreate(ORMBase):
    name: str
    type: str
    location: str
    quiet_score: Optional[float] = None
    is_accessible: bool = False
    capacity: Optional[int] = None


class SpaceRead(SpaceCreate):
    id: uuid.UUID
    created_at: datetime


class SpaceBookingCreate(ORMBase):
    space_id: uuid.UUID
    user_id: uuid.UUID
    start_time: datetime
    end_time: datetime
    status: BookingStatus = BookingStatus.PENDING


class SpaceBookingRead(SpaceBookingCreate):
    id: uuid.UUID
    created_at: datetime


# Housing 
class HousingListingCreate(ORMBase):
    owner_id: uuid.UUID
    title: str
    description: str
    monthly_rent: float
    location: str
    image_urls: Optional[List[str]] = None
    bedrooms: Optional[int] = None
    bathrooms: Optional[int] = None
    is_furnished: bool = False
    is_accessible: bool = False
    available_from: datetime


class HousingListingRead(HousingListingCreate):
    id: uuid.UUID
    created_at: datetime


# Notifications 
class NotificationCreate(ORMBase):
    user_id: uuid.UUID
    title: str
    body: str
    type: str
    scheduled_for: Optional[datetime] = None


class NotificationRead(NotificationCreate):
    id: uuid.UUID
    created_at: datetime
    delivered_at: Optional[datetime] = None
    is_read: bool


# Library 
class LibraryResourceCreate(ORMBase):
    title: str
    author: str
    category: str
    resource_type: str
    location: Optional[str] = None
    is_available: bool = True


class LibraryResourceRead(LibraryResourceCreate):
    id: uuid.UUID
    created_at: datetime


#  Lost & Found 
class LostFoundItemCreate(ORMBase):
    title: str
    description: str
    location: str
    status: str
    reported_by: Optional[uuid.UUID] = None
    image_url: Optional[str] = None


class LostFoundItemRead(LostFoundItemCreate):
    id: uuid.UUID
    reported_at: datetime


#  Knowledge / RAG 
class DocumentCreate(ORMBase):
    title: str
    source_text: str
    uri: str


class DocumentRead(DocumentCreate):
    id: uuid.UUID
    uploaded_at: datetime


class KnowledgeChunkCreate(ORMBase):
    document_id: uuid.UUID
    text: str


class KnowledgeChunkRead(KnowledgeChunkCreate):
    id: uuid.UUID


class VectorEmbeddingCreate(ORMBase):
    chunk_id: uuid.UUID
    # You can post either embedding (list[float]) or text_embedding; your ingestion code decides.
    text_embedding: Optional[str] = None
    embedding: Optional[List[float]] = None


class VectorEmbeddingRead(VectorEmbeddingCreate):
    id: uuid.UUID





class AssistantConversationCreate(ORMBase):
    user_id: Optional[uuid.UUID] = None
    title: Optional[str] = None
    channel: str = "web"
    metadata: Optional[Dict[str, Any]] = None


class AssistantConversationRead(ORMBase):
    id: uuid.UUID
    user_id: Optional[uuid.UUID] = None
    title: Optional[str] = None
    channel: str
    metadata: dict = Field(alias="meta")
    created_at: datetime
    updated_at: datetime


class AssistantMessageCreate(ORMBase):
    conversation_id: uuid.UUID
    role: str
    content: str
    tool_name: Optional[str] = None
    tool_payload: Optional[Dict[str, Any]] = None
    tokens_in: Optional[int] = None
    tokens_out: Optional[int] = None
    latency_ms: Optional[int] = None


class AssistantMessageRead(ORMBase):
    id: uuid.UUID
    conversation_id: uuid.UUID
    role: str
    content: str
    tool_name: Optional[str] = None
    tool_payload: Optional[Dict[str, Any]] = None
    tokens_in: Optional[int] = None
    tokens_out: Optional[int] = None
    latency_ms: Optional[int] = None
    created_at: datetime


class AssistantAgentEventCreate(ORMBase):
    conversation_id: uuid.UUID
    user_id: Optional[uuid.UUID] = None
    event_type: str
    payload: Dict[str, Any]


class AssistantAgentEventRead(ORMBase):
    id: uuid.UUID
    conversation_id: uuid.UUID
    user_id: Optional[uuid.UUID] = None
    event_type: str
    payload: Dict[str, Any]
    created_at: datetime
