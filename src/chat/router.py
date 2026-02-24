# src/chat/router.py
# Register chat router

from fastapi import APIRouter
from src.chat.views import router as chat_views_router

router = APIRouter()
router.include_router(chat_views_router)
