# src/module/knowledge_views.py
import os
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, BackgroundTasks
from sqlalchemy.orm import Session

from src.database import get_db, SessionLocal
from src.module.knowledge_service import (
    embed_pending_chunks,
    embed_all_chunks_until_done,
    search_knowledge,
    knowledge_stats,
)
from src.module.schemas import ORMBase

router = APIRouter(prefix="/knowledge", tags=["Knowledge"])


class EmbedRunRequest(ORMBase):
    limit: int = int(os.getenv("EMBED_WORKER_PULL_LIMIT", "1000"))
    batch_size: int = int(os.getenv("EMBED_BATCH_SIZE", "96"))


class EmbedBackfillRequest(ORMBase):
    pull_limit: int = 5000
    batch_size: int = int(os.getenv("EMBED_BATCH_SIZE", "96"))


class SearchRequest(ORMBase):
    query: str
    top_k: int = 5
    document_id: Optional[UUID] = None


def _run_embedding_job(limit: int, batch_size: int) -> None:
    db = SessionLocal()
    try:
        embed_pending_chunks(db, limit=limit, batch_size=batch_size)
    finally:
        db.close()


def _run_backfill_job(pull_limit: int, batch_size: int) -> None:
    db = SessionLocal()
    try:
        embed_all_chunks_until_done(db, pull_limit=pull_limit, batch_size=batch_size)
    finally:
        db.close()


@router.post("/embed/run")
def run_embedding_worker(payload: EmbedRunRequest, bg: BackgroundTasks):
    bg.add_task(_run_embedding_job, payload.limit, payload.batch_size)
    return {"status": "accepted", "mode": "single-pass", "limit": payload.limit, "batch_size": payload.batch_size}


@router.post("/embed/backfill")
def backfill_embeddings(payload: EmbedBackfillRequest, bg: BackgroundTasks):
    bg.add_task(_run_backfill_job, payload.pull_limit, payload.batch_size)
    return {"status": "accepted", "mode": "until-done", "pull_limit": payload.pull_limit, "batch_size": payload.batch_size}


@router.get("/embed/stats")
def embed_stats(db: Session = Depends(get_db)):
    return knowledge_stats(db)


@router.post("/search")
def search(payload: SearchRequest, db: Session = Depends(get_db)):
    return {"results": search_knowledge(db, payload.query, payload.top_k, payload.document_id)}
