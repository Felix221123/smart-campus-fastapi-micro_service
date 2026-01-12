# OPEN AI SETUP


import os
import time
from typing import List, Optional

from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

_client: Optional[OpenAI] = None


def get_openai_client() -> OpenAI:
    global _client
    if _client is not None:
        return _client

    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY not found. Put it in .env")

    _client = OpenAI(api_key=api_key)
    return _client


def _trim_for_embedding(text: str, max_chars: int = 12000) -> str:
    t = (text or "").strip()
    return t[:max_chars]


def embed_texts(
    texts: List[str],
    model: Optional[str] = None,
    max_retries: int = 5,
) -> List[List[float]]:
    client = get_openai_client()
    embed_model = model or os.getenv("OPENAI_EMBED_MODEL", "text-embedding-3-small")

    clean = [_trim_for_embedding(t) for t in texts]

    for attempt in range(max_retries):
        try:
            resp = client.embeddings.create(model=embed_model, input=clean)
            return [d.embedding for d in resp.data]
        except Exception:
            if attempt == max_retries - 1:
                raise
            time.sleep(min(2**attempt, 20) + 0.2)

    raise RuntimeError("Embedding failed after retries.")


def chat_complete(
    system: str,
    user: str,
    model: Optional[str] = None,
    temperature: float = 0.2,
    max_tokens: int = 400,
) -> str:
    client = get_openai_client()
    chat_model = model or os.getenv("OPENAI_CHAT_MODEL", "gpt-4o-mini")

    resp = client.chat.completions.create(
        model=chat_model,
        temperature=temperature,
        max_tokens=max_tokens,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    )
    return resp.choices[0].message.content or ""
