# src/chat/websocket_manager.py
# Manages WebSocket connections, online presence, and message broadcasting

from typing import Dict, List, Set
from uuid import UUID
from fastapi import WebSocket
import json


class ConnectionManager:
    def __init__(self):
        # user_id -> list of active WebSocket connections
        self.active_connections: Dict[UUID, List[WebSocket]] = {}

        # chat_room_id -> set of user_ids currently in that room
        self.room_participants: Dict[UUID, Set[UUID]] = {}

    async def connect(self, websocket: WebSocket, user_id: UUID):
        """Connect a user's WebSocket"""
        await websocket.accept()

        if user_id not in self.active_connections:
            self.active_connections[user_id] = []

        self.active_connections[user_id].append(websocket)

    def disconnect(self, websocket: WebSocket, user_id: UUID):
        """Disconnect a user's WebSocket"""
        if user_id in self.active_connections:
            if websocket in self.active_connections[user_id]:
                self.active_connections[user_id].remove(websocket)

            # Clean up if no more connections
            if not self.active_connections[user_id]:
                del self.active_connections[user_id]

    def join_room(self, user_id: UUID, room_id: UUID):
        """Add user to a chat room (for presence tracking)"""
        if room_id not in self.room_participants:
            self.room_participants[room_id] = set()

        self.room_participants[room_id].add(user_id)

    def leave_room(self, user_id: UUID, room_id: UUID):
        """Remove user from a chat room"""
        if room_id in self.room_participants:
            self.room_participants[room_id].discard(user_id)

            # Clean up empty rooms
            if not self.room_participants[room_id]:
                del self.room_participants[room_id]

    def is_user_online(self, user_id: UUID) -> bool:
        """Check if user is online"""
        return user_id in self.active_connections and len(self.active_connections[user_id]) > 0

    def get_online_users_in_room(self, room_id: UUID) -> List[UUID]:
        """Get list of online users in a specific room"""
        if room_id not in self.room_participants:
            return []

        return [
            user_id
            for user_id in self.room_participants[room_id]
            if self.is_user_online(user_id)
        ]

    async def send_personal_message(self, message: dict, user_id: UUID):
        """Send message to a specific user (all their connections)"""
        if user_id in self.active_connections:
            disconnected = []
            for connection in self.active_connections[user_id]:
                try:
                    await connection.send_json(message)
                except Exception:
                    disconnected.append(connection)

            # Clean up dead connections
            for conn in disconnected:
                self.disconnect(conn, user_id)

    async def broadcast_to_room(self, message: dict, room_id: UUID, sender_id: UUID = None):
        """Broadcast message to all participants in a room (except sender if specified)"""
        if room_id not in self.room_participants:
            return

        for user_id in self.room_participants[room_id]:
            # Skip sender if specified
            if sender_id and user_id == sender_id:
                continue

            await self.send_personal_message(message, user_id)

    async def broadcast_typing(self, room_id: UUID, user_id: UUID, is_typing: bool):
        """Broadcast typing indicator to room participants"""
        message = {
            "type": "typing",
            "data": {
                "room_id": str(room_id),
                "user_id": str(user_id),
                "is_typing": is_typing
            }
        }
        await self.broadcast_to_room(message, room_id, sender_id=user_id)

    async def broadcast_presence(self, user_id: UUID, status: str):
        """Broadcast user online/offline status to all their chat rooms"""
        message = {
            "type": "presence",
            "data": {
                "user_id": str(user_id),
                "status": status  # "online" | "offline"
            }
        }

        # Find all rooms this user is in and broadcast
        for room_id, participants in self.room_participants.items():
            if user_id in participants:
                await self.broadcast_to_room(message, room_id, sender_id=user_id)


# Global connection manager instance
manager = ConnectionManager()
