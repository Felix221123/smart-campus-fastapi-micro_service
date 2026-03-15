# Define all the router here.

from fastapi import APIRouter
from .module.views import router as module_router
from .chat.router import router as chat_router
from .users.views import router as users_router
from .analytics.views import router as analytics_router

router = APIRouter()
router.include_router(module_router)
router.include_router(chat_router)
router.include_router(users_router)
router.include_router(analytics_router)


