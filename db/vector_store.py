"""
PgVector Store — drop-in replacement for ChromaDB query interface.

Combines vector similarity (cosine) with BM25 full-text search,
then reranks by weighted combination.

Usage:
    from db.vector_store import PgVectorStore
    store = PgVectorStore()
    results = store.query("papers", "gold hedge safe haven", n_results=5,
                          where={"authority_tier": 2},
                          as_of_date="2023-01-01")
"""

import hashlib
import json
from typing import Optional

from sentence_transformers import SentenceTransformer

from db.connection import db_cursor, is_available

# Lazy-loaded embedding model (same as ChromaDB ingestion)
_embed_model = None


def _get_embed_model():
    global _embed_model
    if _embed_model is None:
        _embed_model = SentenceTransformer('nomic-ai/nomic-embed-text-v1',
                                           trust_remote_code=True)
    return _embed_model


def _embed(text: str) -> list:
    model = _get_embed_model()
    return model.encode(text).tolist()


class PgVectorStore:
    """Postgres + pgvector store with hybrid vector + BM25 search."""

    def query(self, collection: str, query_text: str, n_results: int = 5,
              where: dict = None, as_of_date: str = None,
              vector_weight: float = 0.7, bm25_weight: float = 0.3) -> dict:
        """Query with hybrid vector + BM25 search.

        Returns dict matching ChromaDB format:
            {documents: [[str]], metadatas: [[dict]], distances: [[float]], ids: [[str]]}
        """
        query_embedding = _embed(query_text)

        # Build WHERE clause
        conditions = ["collection = %s"]
        params = [collection]

        if as_of_date:
            date_int = int(as_of_date.replace("-", ""))
            conditions.append("published_date_int <= %s")
            params.append(date_int)

        if where:
            for key, val in where.items():
                if isinstance(val, dict):
                    for op, v in val.items():
                        sql_op = {"$lte": "<=", "$gte": ">=", "$eq": "=",
                                  "$lt": "<", "$gt": ">"}.get(op, "=")
                        conditions.append(f"{key} {sql_op} %s")
                        params.append(v)
                else:
                    conditions.append(f"{key} = %s")
                    params.append(val)

        where_sql = " AND ".join(conditions)

        # Hybrid query: vector similarity + BM25
        sql = f"""
            WITH vector_scores AS (
                SELECT doc_id, chunk_ix, chunk_text, author, title, page,
                       source_id, authority_tier, asset_class,
                       published_date_int,
                       1 - (embedding <=> %s::vector) AS vec_score
                FROM document_chunks
                WHERE {where_sql}
                ORDER BY embedding <=> %s::vector
                LIMIT %s * 3
            ),
            bm25_scores AS (
                SELECT doc_id, chunk_ix,
                       ts_rank_cd(tsv, plainto_tsquery('english', %s)) AS bm25_score
                FROM document_chunks
                WHERE {where_sql}
                  AND tsv @@ plainto_tsquery('english', %s)
            )
            SELECT v.doc_id, v.chunk_ix, v.chunk_text, v.author, v.title,
                   v.page, v.source_id, v.authority_tier, v.asset_class,
                   v.published_date_int,
                   ({vector_weight} * v.vec_score +
                    {bm25_weight} * COALESCE(b.bm25_score, 0)) AS combined_score
            FROM vector_scores v
            LEFT JOIN bm25_scores b
                ON v.doc_id = b.doc_id AND v.chunk_ix = b.chunk_ix
            ORDER BY combined_score DESC
            LIMIT %s
        """

        # Params: embedding, collection+where, embedding, limit*3,
        #         query_text, collection+where, query_text, limit
        query_params = (
            [str(query_embedding)] + params + [str(query_embedding), n_results * 3] +
            [query_text] + params + [query_text] +
            [n_results]
        )

        with db_cursor(commit=False) as cur:
            cur.execute(sql, query_params)
            rows = cur.fetchall()

        # Format as ChromaDB-compatible response
        documents = []
        metadatas = []
        distances = []
        ids = []

        for row in rows:
            doc_id, chunk_ix, text, author, title, page, source_id, \
                tier, ac, pub_int, score = row
            documents.append(text)
            metadatas.append({
                "author": author or "Unknown",
                "title": title or "Unknown",
                "page": page or 0,
                "source_id": source_id or "",
                "authority_tier": tier,
                "asset_class": ac,
                "published_date_int": pub_int,
            })
            distances.append(round(1 - score, 4))  # Convert similarity to distance
            ids.append(f"{doc_id}_{chunk_ix}")

        return {
            "documents": [documents],
            "metadatas": [metadatas],
            "distances": [distances],
            "ids": [ids],
        }

    def add(self, collection: str, documents: list, metadatas: list,
            ids: list, embeddings: list = None) -> int:
        """Insert chunks with embeddings."""
        if embeddings is None or len(embeddings) == 0:
            model = _get_embed_model()
            embeddings = model.encode(documents).tolist()

        def _sanitize(s):
            """Strip NUL bytes that Postgres text columns can't store."""
            return s.replace('\x00', '') if isinstance(s, str) else s

        inserted = 0
        with db_cursor() as cur:
            for doc, meta, id_, raw_emb in zip(documents, metadatas, ids, embeddings):
                emb = raw_emb.tolist() if hasattr(raw_emb, 'tolist') else list(raw_emb)
                # Parse doc_id and chunk_ix from id string
                parts = id_.rsplit("_", 1)
                doc_id = parts[0]
                chunk_ix = int(parts[1]) if len(parts) > 1 else 0

                pub_date = meta.get("published_date")
                pub_int = meta.get("published_date_int")
                if pub_date and not pub_int:
                    pub_int = int(pub_date.replace("-", ""))

                clean_doc = _sanitize(doc)
                checksum = hashlib.md5(clean_doc.encode()).hexdigest()

                # Sanitize all metadata string values
                clean_meta = {k: _sanitize(v) if isinstance(v, str) else v
                              for k, v in meta.items()}

                cur.execute("""
                    INSERT INTO document_chunks
                        (doc_id, chunk_ix, collection, source_class, authority_tier,
                         asset_class, published_date_int, embedding, chunk_text,
                         author, title, page, source_id, checksum, metadata)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s::vector, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (doc_id, chunk_ix) DO UPDATE SET
                        chunk_text = EXCLUDED.chunk_text,
                        embedding = EXCLUDED.embedding,
                        metadata = EXCLUDED.metadata
                """, (
                    doc_id, chunk_ix, collection,
                    clean_meta.get("document_type", "book"),
                    clean_meta.get("authority_tier", 3),
                    clean_meta.get("asset_class", "general"),
                    pub_int,
                    str(emb),
                    clean_doc,
                    _sanitize(meta.get("author", "Unknown")),
                    _sanitize(meta.get("title", "Unknown")),
                    meta.get("page", 0),
                    _sanitize(meta.get("source_id", "")),
                    checksum,
                    _sanitize(json.dumps(clean_meta)),
                ))
                inserted += 1

        return inserted

    def delete(self, collection: str, source_id: str = None,
               where: dict = None) -> int:
        """Delete chunks matching filter."""
        conditions = ["collection = %s"]
        params = [collection]

        if source_id:
            conditions.append("source_id = %s")
            params.append(source_id)

        if where:
            for key, val in where.items():
                conditions.append(f"{key} = %s")
                params.append(val)

        where_sql = " AND ".join(conditions)
        with db_cursor() as cur:
            cur.execute(f"DELETE FROM document_chunks WHERE {where_sql}", params)
            return cur.rowcount

    def count(self, collection: str = None) -> int:
        """Count chunks, optionally filtered by collection."""
        with db_cursor(commit=False) as cur:
            if collection:
                cur.execute("SELECT COUNT(*) FROM document_chunks WHERE collection = %s",
                            (collection,))
            else:
                cur.execute("SELECT COUNT(*) FROM document_chunks")
            return cur.fetchone()[0]

    def get_stats(self) -> dict:
        """Get corpus statistics."""
        stats = {"total": 0, "by_collection": {}, "by_tier": {}, "by_asset_class": {}}
        with db_cursor(commit=False) as cur:
            cur.execute("SELECT COUNT(*) FROM document_chunks")
            stats["total"] = cur.fetchone()[0]

            cur.execute("""
                SELECT collection, COUNT(*) FROM document_chunks
                GROUP BY collection ORDER BY collection
            """)
            stats["by_collection"] = dict(cur.fetchall())

            cur.execute("""
                SELECT authority_tier, COUNT(*) FROM document_chunks
                GROUP BY authority_tier ORDER BY authority_tier
            """)
            stats["by_tier"] = dict(cur.fetchall())

            cur.execute("""
                SELECT asset_class, COUNT(*) FROM document_chunks
                GROUP BY asset_class ORDER BY asset_class
            """)
            stats["by_asset_class"] = dict(cur.fetchall())

        return stats
