# src/chat/models.py
# SQLAlchemy models for ChatRoom and Message (matching your Express TypeORM entities)

import os
import uuid
from datetime import datetime
from enum import Enum as PyEnum

from sqlalchemy import (
    Column,
    String,
    DateTime,
    Text,
    ForeignKey,
    Table,
    Enum as SAEnum,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from src.module.models import Base, User

DB_SCHEMA = os.getenv("DB_SCHEMA", "public")



# ChatRoomType Enum

class ChatRoomType(str, PyEnum):
    DIRECT = "DIRECT"
    GROUP = "GROUP"



# Association Table: chat_room_participants

chat_room_participants = Table(
    "chat_room_participants",
    Base.metadata,
    Column("chat_room_id", UUID(as_uuid=True), ForeignKey(f"{DB_SCHEMA}.chat_rooms.id", ondelete="CASCADE"), primary_key=True),
    Column("user_id", UUID(as_uuid=True), ForeignKey(f"{DB_SCHEMA}.users.id", ondelete="CASCADE"), primary_key=True),
    schema=DB_SCHEMA
)



# ChatRoom Model

class ChatRoom(Base):
    __tablename__ = "chat_rooms"
    __table_args__ = {"schema": DB_SCHEMA}

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    type = Column(
        SAEnum(ChatRoomType, name="chatroomtype"),
        nullable=False,
        default=ChatRoomType.GROUP
    )

    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    # Relationships
    participants = relationship(
        "User",
        secondary=chat_room_participants,
        backref="chat_rooms"
    )

    messages = relationship(
        "Message",
        back_populates="chat_room",
        cascade="all, delete-orphan",
        order_by="Message.sent_at"
    )



# Message Model

class Message(Base):
    __tablename__ = "messages"
    __table_args__ = {"schema": DB_SCHEMA}

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    chat_room_id = Column(
        "chatRoomId",
        UUID(as_uuid=True),
        ForeignKey(f"{DB_SCHEMA}.chat_rooms.id", ondelete="CASCADE"),
        nullable=False,
        index=True
    )

    sender_id = Column(
        "senderId",
        UUID(as_uuid=True),
        ForeignKey(f"{DB_SCHEMA}.users.id", ondelete="CASCADE"),
        nullable=False,
        index=True
    )

    content = Column(Text, nullable=False)

    sent_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    # Relationships
    chat_room = relationship("ChatRoom", back_populates="messages")
    sender = relationship("User")
