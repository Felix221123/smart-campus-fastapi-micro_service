# Define your module's routes here

from fastapi import APIRouter
from src.module.knowledge_views import router as knowledge_router
from src.module.assistant_views import router as assistant_router


router = APIRouter()


@router.get("/")
async def root():
    return {"message": "Hello World"}



router.include_router(knowledge_router)
router.include_router(assistant_router)