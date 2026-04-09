# src/chat/views.py
# REST API and WebSocket endpoints for chat functionality

from typing import List, Optional
from uuid import UUID
from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect, Query, status
from sqlalchemy.orm import Session

from src.database import get_db, get_db_session
from src.auth import get_current_user, get_current_user_ws
from src.module.models import User
from src.chat.schemas import (
    ChatRoomCreate,
    ChatRoomResponse,
    ChatRoomDetail,
    MessageCreate,
    MessageResponse,
    WSMessageSend,
)
from src.chat.service import ChatService
from src.chat.websocket_manager import manager
import json


router = APIRouter(prefix="/chat", tags=["Chat"])



# REST API Endpoints


@router.post("/rooms", response_model=ChatRoomResponse, status_code=status.HTTP_201_CREATED)
def create_chat_room(
    data: ChatRoomCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Create a new chat room (direct or group).
    For direct chats, returns existing room if it already exists.
    """
    room = ChatService.create_chat_room(db, data, current_user.id)

    # Prepare response
    response = ChatRoomResponse(
        id=room.id,
        type=room.type,
        created_at=room.created_at,
        participants=room.participants,
        last_message=None
    )

    return response


@router.get("/rooms", response_model=List[ChatRoomResponse])
def get_my_chat_rooms(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Get all chat rooms for the current user"""
    rooms = ChatService.get_user_chat_rooms(db, current_user.id)

    # Convert to response format
    response = []
    for room in rooms:
        response.append(ChatRoomResponse(
            id=room.id,
            type=room.type,
            created_at=room.created_at,
            participants=room.participants,
            last_message=room.last_message
        ))

    return response


@router.get("/rooms/{room_id}", response_model=ChatRoomDetail)
def get_chat_room_detail(
    room_id: UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Get chat room details with all messages"""
    room = ChatService.get_chat_room_detail(db, room_id, current_user.id)

    return ChatRoomDetail(
        id=room.id,
        type=room.type,
        created_at=room.created_at,
        participants=room.participants,
        messages=room.messages
    )


@router.post("/rooms/{room_id}/messages", response_model=MessageResponse, status_code=status.HTTP_201_CREATED)
def send_message(
    room_id: UUID,
    data: MessageCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Send a message to a chat room"""
    message = ChatService.send_message(db, room_id, current_user.id, data)

    return MessageResponse(
        id=message.id,
        chat_room_id=message.chat_room_id,
        sender_id=message.sender_id,
        content=message.content,
        sent_at=message.sent_at,
        sender=message.sender
    )


@router.get("/rooms/{room_id}/messages", response_model=List[MessageResponse])
def get_chat_history(
    room_id: UUID,
    limit: int = Query(50, ge=1, le=100),
    before_message_id: Optional[UUID] = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Get paginated chat history"""
    messages = ChatService.get_chat_history(
        db, room_id, current_user.id, limit, before_message_id
    )

    return [
        MessageResponse(
            id=msg.id,
            chat_room_id=msg.chat_room_id,
            sender_id=msg.sender_id,
            content=msg.content,
            sent_at=msg.sent_at,
            sender=msg.sender
        )
        for msg in messages
    ]



# WebSocket Endpoint
@router.websocket("/ws")
async def websocket_endpoint(
    websocket: WebSocket,
    token: str = Query(...),
):
    """
    WebSocket endpoint for real-time chat.
     FIXED: Manual DB session management to prevent connection pool exhaustion
    """

    #  Create a dedicated DB session for authentication
    db = get_db_session()
    try:
        user = await get_current_user_ws(token, db)
    except ValueError as e:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION, reason=str(e))
        return
    finally:
        db.close()  #  Close auth session immediately

    # Connect user
    await manager.connect(websocket, user.id)

    # Broadcast online status
    await manager.broadcast_presence(user.id, "online")

    try:
        while True:
            # Receive message from client
            data = await websocket.receive_text()
            message_data = json.loads(data)

            message_type = message_data.get("type")
            payload = message_data.get("data", {})

            if message_type == "message":
                #  Create NEW session for each message operation
                db = get_db_session()
                try:
                    room_id = UUID(payload["chat_room_id"])
                    content = payload["content"]

                    # Save message to database
                    message = ChatService.send_message(
                        db,
                        room_id,
                        user.id,
                        MessageCreate(content=content)
                    )

                    # Broadcast to room participants
                    message_payload = {
                        "type": "message",
                        "data": {
                            "id": str(message.id),
                            "chat_room_id": str(message.chat_room_id),
                            "sender_id": str(message.sender_id),
                            "content": message.content,
                            "sent_at": message.sent_at.isoformat(),
                            "sender": {
                                "id": str(message.sender.id),
                                "full_name": message.sender.full_name,
                                "email": message.sender.email,
                                "role": message.sender.role
                            }
                        }
                    }

                    # Send canonical message back to sender too
                    await manager.send_personal_message(message_payload, user.id)

                    # Send to everyone else in room
                    await manager.broadcast_to_room(
                        message_payload,
                        room_id,
                        sender_id=user.id
                    )

                except Exception as e:
                    await websocket.send_json({
                        "type": "error",
                        "data": {"message": str(e)}
                    })
                finally:
                    db.close()  #  Always close session

            elif message_type == "typing":
                # Typing doesn't need DB - no session needed
                try:
                    room_id = UUID(payload["chat_room_id"])
                    is_typing = payload.get("is_typing", False)

                    await manager.broadcast_typing(room_id, user.id, is_typing)

                except Exception as e:
                    await websocket.send_json({
                        "type": "error",
                        "data": {"message": str(e)}
                    })

            elif message_type == "join_room":
                # User joins a room (for presence tracking)
                try:
                    room_id = UUID(payload["chat_room_id"])
                    manager.join_room(user.id, room_id)

                    # Send online users in room
                    online_users = manager.get_online_users_in_room(room_id)
                    await websocket.send_json({
                        "type": "room_joined",
                        "data": {
                            "room_id": str(room_id),
                            "online_users": [str(uid) for uid in online_users]
                        }
                    })

                except Exception as e:
                    await websocket.send_json({
                        "type": "error",
                        "data": {"message": str(e)}
                    })

            elif message_type == "leave_room":
                # User leaves a room
                try:
                    room_id = UUID(payload["chat_room_id"])
                    manager.leave_room(user.id, room_id)

                except Exception as e:
                    await websocket.send_json({
                        "type": "error",
                        "data": {"message": str(e)}
                    })

    except WebSocketDisconnect:
        # Handle disconnect
        manager.disconnect(websocket, user.id)
        await manager.broadcast_presence(user.id, "offline")
