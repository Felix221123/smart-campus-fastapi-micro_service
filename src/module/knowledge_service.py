
import os
from typing import Optional, List, Dict, Any
from uuid import UUID

from sqlalchemy.orm import Session
from sqlalchemy import func
from sqlalchemy import func, exists, and_


from src.module.models import Document, KnowledgeChunk, VectorEmbedding
from src.utils.openai_client import embed_texts
from src.utils.cache import get_cache, make_cache_key

# checking the stats of the embedded data
def knowledge_stats(db: Session) -> Dict[str, int]:
    total_docs = db.query(func.count(Document.id)).scalar() or 0
    total_chunks = db.query(func.count(KnowledgeChunk.id)).scalar() or 0
    embedded_chunks = (
        db.query(func.count(VectorEmbedding.id))
        .filter(VectorEmbedding.embedding.isnot(None))
        .scalar()
        or 0
    )
    return {
        "documents": int(total_docs),
        "chunks": int(total_chunks),
        "embedded_chunks": int(embedded_chunks),
    }


# checking for any pending chunks and embedding them
def embed_pending_chunks(
    db: Session,
    limit: int = 1000,
    batch_size: int = 96,
) -> Dict[str, Any]:
    """
    Backfill embeddings for KnowledgeChunk rows that are missing a vector embedding.
    Uses SKIP LOCKED safely (no OUTER JOIN).
    """

    # pending := chunks where there does NOT exist a vector_embeddings row
    # with same chunk_id AND embedding IS NOT NULL
    pending_q = (
        db.query(KnowledgeChunk.id, KnowledgeChunk.text)
        .filter(
            ~exists().where(
                and_(
                    VectorEmbedding.chunk_id == KnowledgeChunk.id,
                    VectorEmbedding.embedding.isnot(None),
                )
            )
        )
        .order_by(KnowledgeChunk.id.asc())
        .limit(limit)
        .with_for_update(skip_locked=True, of=KnowledgeChunk)  # lock only chunks
    )

    pending_rows = pending_q.all()

    if not pending_rows:
        return {"embedded": 0, "batches": 0}

    embedded = 0
    batches = 0

    for i in range(0, len(pending_rows), batch_size):
        batch = pending_rows[i : i + batch_size]
        chunk_ids = [cid for (cid, _t) in batch]
        texts = [t or "" for (_cid, t) in batch]

        vectors = embed_texts(texts)

        # Fetch existing vector rows for this batch in ONE query
        existing_rows = (
            db.query(VectorEmbedding)
            .filter(VectorEmbedding.chunk_id.in_(chunk_ids))
            .all()
        )
        existing_map = {row.chunk_id: row for row in existing_rows}

        # Upsert
        for chunk_id, vec in zip(chunk_ids, vectors):
            row = existing_map.get(chunk_id)
            if row is None:
                db.add(VectorEmbedding(chunk_id=chunk_id, embedding=vec))
            else:
                row.embedding = vec

        db.commit()
        embedded += len(batch)
        batches += 1

    return {"embedded": embedded, "batches": batches}



# embedding all chunks till completed
def embed_all_chunks_until_done(
    db: Session,
    pull_limit: int = 5000,
    batch_size: int = 96,
    max_loops: int = 10_000,
) -> Dict[str, Any]:
    """
    Keeps embedding until no pending chunks remain.
    Best run in BackgroundTasks.
    """
    total_embedded = 0
    loops = 0

    while loops < max_loops:
        res = embed_pending_chunks(db, limit=pull_limit, batch_size=batch_size)
        loops += 1
        total_embedded += int(res.get("embedded", 0))
        if res.get("embedded", 0) == 0:
            break

    return {"total_embedded": total_embedded, "loops": loops}


# searching through the knowledge
def search_knowledge(
    db: Session,
    query: str,
    top_k: int = 5,
    document_id: Optional[UUID] = None,
) -> List[Dict[str, Any]]:
    """
    Embed query -> cosine similarity search -> top-k chunks.
    Cached for speed.
    """
    cache = get_cache()
    key = make_cache_key("knowledge_search", query, str(top_k), str(document_id or ""))
    cached = cache.get(key)
    if cached is not None:
        return cached

    qvec = embed_texts([query])[0]
    dist = VectorEmbedding.embedding.cosine_distance(qvec)

    q = (
        db.query(KnowledgeChunk, Document, dist.label("distance"))
        .join(VectorEmbedding, VectorEmbedding.chunk_id == KnowledgeChunk.id)
        .join(Document, Document.id == KnowledgeChunk.document_id)
        .filter(VectorEmbedding.embedding.isnot(None))
    )

    if document_id:
        q = q.filter(KnowledgeChunk.document_id == document_id)

    rows = q.order_by(dist.asc()).limit(top_k).all()

    results: List[Dict[str, Any]] = []
    for chunk, doc, distance in rows:
        similarity = 1.0 - float(distance)
        results.append(
            {
                "chunk_id": str(chunk.id),
                "document_id": str(doc.id),
                "document_title": doc.title,
                "uri": str(doc.uri),
                "text": chunk.text,
                "similarity": similarity,
            }
        )

    cache.set(key, results, ttl_seconds=60)
    return results
