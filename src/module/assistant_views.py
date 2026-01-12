# src/module/assistant_views.py
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from src.database import get_db
from src.module.schemas import ORMBase
from src.module.assistant_service import answer_question

router = APIRouter(tags=["Assistant"])


class AskRequest(ORMBase):
    question: str


@router.post("/ask")
def ask(payload: AskRequest, db: Session = Depends(get_db)):
    return answer_question(db, payload.question)
