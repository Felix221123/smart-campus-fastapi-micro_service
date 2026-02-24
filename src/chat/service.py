# src/chat/service.py
# Business logic for chat operations

from typing import List, Optional
from uuid import UUID
from sqlalchemy.orm import Session, joinedload
from sqlalchemy import and_, or_, desc
from fastapi import HTTPException, status

from src.chat.models import ChatRoom, Message, ChatRoomType
from src.module.models import User
from src.chat.schemas import ChatRoomCreate, MessageCreate


class ChatService:

    @staticmethod
    def create_chat_room(db: Session, data: ChatRoomCreate, creator_id: UUID) -> ChatRoom:
        """Create a new chat room (direct or group)"""

        # Validate participants exist
        participants = db.query(User).filter(User.id.in_(data.participant_ids)).all()

        if len(participants) != len(data.participant_ids):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="One or more participants not found"
            )

        # For DIRECT chats, ensure only 2 participants
        if data.type == ChatRoomType.DIRECT and len(data.participant_ids) != 2:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Direct chats must have exactly 2 participants"
            )

        # Check if direct chat already exists between these users
        if data.type == ChatRoomType.DIRECT:
            existing_room = ChatService._find_existing_direct_chat(
                db, data.participant_ids[0], data.participant_ids[1]
            )
            if existing_room:
                return existing_room

        # Create chat room
        chat_room = ChatRoom(type=data.type)
        chat_room.participants = participants

        db.add(chat_room)
        db.commit()
        db.refresh(chat_room)

        return chat_room

    @staticmethod
    def _find_existing_direct_chat(db: Session, user1_id: UUID, user2_id: UUID) -> Optional[ChatRoom]:
        """Find existing direct chat between two users"""
        # Query for DIRECT chat rooms that include both users
        rooms = (
            db.query(ChatRoom)
            .filter(ChatRoom.type == ChatRoomType.DIRECT)
            .join(ChatRoom.participants)
            .filter(User.id.in_([user1_id, user2_id]))
            .all()
        )

        # Check if any room has exactly these two participants
        for room in rooms:
            participant_ids = {p.id for p in room.participants}
            if participant_ids == {user1_id, user2_id}:
                return room

        return None

    @staticmethod
    def get_user_chat_rooms(db: Session, user_id: UUID) -> List[ChatRoom]:
        """Get all chat rooms for a user with last message"""
        rooms = (
            db.query(ChatRoom)
            .join(ChatRoom.participants)
            .filter(User.id == user_id)
            .options(
                joinedload(ChatRoom.participants),
                joinedload(ChatRoom.messages).joinedload(Message.sender)
            )
            .all()
        )

        # Sort by last message time
        for room in rooms:
            room.last_message = room.messages[-1] if room.messages else None

        rooms.sort(
            key=lambda r: r.last_message.sent_at if r.last_message else r.created_at,
            reverse=True
        )

        return rooms

    @staticmethod
    def get_chat_room_detail(db: Session, room_id: UUID, user_id: UUID) -> ChatRoom:
        """Get chat room with all messages"""
        room = (
            db.query(ChatRoom)
            .filter(ChatRoom.id == room_id)
            .options(
                joinedload(ChatRoom.participants),
                joinedload(ChatRoom.messages).joinedload(Message.sender)
            )
            .first()
        )

        if not room:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Chat room not found"
            )

        # Verify user is participant
        participant_ids = [p.id for p in room.participants]
        if user_id not in participant_ids:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You are not a participant in this chat room"
            )

        return room

    @staticmethod
    def send_message(db: Session, room_id: UUID, sender_id: UUID, data: MessageCreate) -> Message:
        """Send a message to a chat room"""

        # Verify chat room exists and user is participant
        room = db.query(ChatRoom).filter(ChatRoom.id == room_id).first()

        if not room:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Chat room not found"
            )

        participant_ids = [p.id for p in room.participants]
        if sender_id not in participant_ids:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You are not a participant in this chat room"
            )

        # Create message
        message = Message(
            chat_room_id=room_id,
            sender_id=sender_id,
            content=data.content
        )

        db.add(message)
        db.commit()
        db.refresh(message)

        # Load sender relationship
        message.sender = db.query(User).filter(User.id == sender_id).first()

        return message

    @staticmethod
    def get_chat_history(
        db: Session,
        room_id: UUID,
        user_id: UUID,
        limit: int = 50,
        before_message_id: Optional[UUID] = None
    ) -> List[Message]:
        """Get paginated chat history"""

        # Verify user is participant
        room = db.query(ChatRoom).filter(ChatRoom.id == room_id).first()
        if not room:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Chat room not found"
            )

        participant_ids = [p.id for p in room.participants]
        if user_id not in participant_ids:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You are not a participant in this chat room"
            )

        # Build query
        query = (
            db.query(Message)
            .filter(Message.chat_room_id == room_id)
            .options(joinedload(Message.sender))
            .order_by(desc(Message.sent_at))
        )

        # Pagination: get messages before a specific message
        if before_message_id:
            before_message = db.query(Message).filter(Message.id == before_message_id).first()
            if before_message:
                query = query.filter(Message.sent_at < before_message.sent_at)

        messages = query.limit(limit).all()
        messages.reverse()  # Return in chronological order

        return messages
