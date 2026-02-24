# src/chat/schemas.py
# Pydantic schemas for request/response validation

from datetime import datetime
from typing import List, Optional
from uuid import UUID
from pydantic import BaseModel, Field
from src.chat.models import ChatRoomType



# User Schemas (for nested responses)

class UserBasic(BaseModel):
    id: UUID
    full_name: str
    email: str
    role: str

    class Config:
        from_attributes = True
        json_encoders = {
            UUID: lambda v: str(v)
        }



# Message Schemas

class MessageCreate(BaseModel):
    content: str


class MessageResponse(BaseModel):
    id: UUID
    chat_room_id: UUID
    sender_id: UUID
    content: str
    sent_at: datetime
    sender: UserBasic

    class Config:
        from_attributes = True
        json_encoders = {
            UUID: lambda v: str(v)
        }



# ChatRoom Schemas

class ChatRoomCreate(BaseModel):
    type: ChatRoomType
    participant_ids: List[UUID]   


class ChatRoomResponse(BaseModel):
    id: UUID
    type: ChatRoomType
    created_at: datetime
    participants: List[UserBasic]
    last_message: Optional[MessageResponse] = None

    class Config:
        from_attributes = True
        json_encoders = {
            UUID: lambda v: str(v)
        }


class ChatRoomDetail(BaseModel):
    id: UUID
    type: ChatRoomType
    created_at: datetime
    participants: List[UserBasic]
    messages: List[MessageResponse]

    class Config:
        from_attributes = True
        json_encoders = {
            UUID: lambda v: str(v)
        }



# WebSocket Schemas

class WSMessageSend(BaseModel):
    chat_room_id: str
    content: str


class WSMessageReceive(BaseModel):
    type: str  # "message" | "typing" | "online" | "offline"
    data: dict


class WSTypingIndicator(BaseModel):
    chat_room_id: str
    is_typing: bool
