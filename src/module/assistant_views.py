# src/module/assistant_views.py
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from src.database import get_db
from src.module.schemas import ORMBase

from src.llm.orchestrator import run as llm_run
from typing import Optional

router = APIRouter(tags=["Assistant"])


class AskRequest(ORMBase):
    question: str
    conversation_id: Optional[str] = None
    user_id: Optional[str] = None
    channel: str = "web"


@router.post("/ask")
def ask(payload: AskRequest, db: Session = Depends(get_db)):
    # user_id: pass from auth later; for now None
    return llm_run(db=db, question=payload.question, user_id=None, conversation_id=None or payload.conversation_id, channel="web")