from __future__ import annotations

import logging
from typing import Any

from wow_core import settings

logger = logging.getLogger(__name__)


def _client():
    import chromadb
    from chromadb.utils.embedding_functions import SentenceTransformerEmbeddingFunction

    settings.ensure_data_dirs()
    embedding_fn = SentenceTransformerEmbeddingFunction(
        model_name=settings.EMBEDDING_MODEL,
    )
    client = chromadb.PersistentClient(path=str(settings.CHROMA_DIR))
    return client.get_or_create_collection(
        name=settings.CHROMA_COLLECTION,
        embedding_function=embedding_fn,
        metadata={"hnsw:space": "cosine"},
    )


def upsert_summary(
    *,
    video_id: str,
    summary: str,
    channel_id: str,
    channel_name: str,
    published_date: str,
    topics: list[str],
    relevance: str,
    title: str,
) -> None:
    collection = _client()
    collection.upsert(
        ids=[video_id],
        documents=[summary],
        metadatas=[
            {
                "channel_id": channel_id,
                "channel_name": channel_name,
                "published_date": published_date,
                "topics": ",".join(topics),
                "relevance": relevance,
                "title": title,
            }
        ],
    )


def query_similar(text: str, *, top_k: int | None = None) -> list[dict[str, Any]]:
    collection = _client()
    k = settings.SEMANTIC_TOP_K if top_k is None else top_k
    if collection.count() == 0:
        return []
    result = collection.query(
        query_texts=[text],
        n_results=min(k, collection.count()),
        include=["documents", "metadatas", "distances"],
    )
    ids = (result.get("ids") or [[]])[0]
    docs = (result.get("documents") or [[]])[0]
    metas = (result.get("metadatas") or [[]])[0]
    distances = (result.get("distances") or [[]])[0]
    ranked: list[dict[str, Any]] = []
    for video_id, document, metadata, distance in zip(ids, docs, metas, distances):
        ranked.append(
            {
                "video_id": video_id,
                "summary": document,
                "metadata": metadata or {},
                "distance": distance,
            }
        )
    return ranked
