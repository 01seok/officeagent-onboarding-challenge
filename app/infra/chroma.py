import chromadb
from chromadb import Collection


class ChromaClient:
    COLLECTION_NAME = "document_chunks"

    def __init__(self, persist_dir: str):
        self._client = chromadb.PersistentClient(path=persist_dir)
        self._collection: Collection = self._client.get_or_create_collection(
            name=self.COLLECTION_NAME,
            metadata={"hnsw:space": "cosine"},  # 코사인 거리 기반 벡터 인덱스
        )

    def add_chunks(
        self,
        chunk_ids: list[str],
        embeddings: list[list[float]],
        documents: list[str],
        metadatas: list[dict],
    ) -> None:
        self._collection.add(
            ids=chunk_ids,
            embeddings=embeddings,
            documents=documents,
            metadatas=metadatas,
        )

    def vector_search(
        self,
        query_embedding: list[float],
        n_results: int = 100,
        where: dict | None = None,
    ) -> list[dict]:
        kwargs: dict = {
            "query_embeddings": [query_embedding],
            "n_results": n_results,
            "include": ["documents", "metadatas", "distances"],
        }
        if where:
            kwargs["where"] = where

        result = self._collection.query(**kwargs)
        chunks = []
        for i, chunk_id in enumerate(result["ids"][0]):
            chunks.append(
                {
                    "chunk_id": chunk_id,
                    "document": result["documents"][0][i],
                    "metadata": result["metadatas"][0][i],
                    # cosine distance → similarity (1 - distance)
                    "score": 1 - result["distances"][0][i],
                }
            )
        return chunks

    def get_all_chunks(self, doc_id: str) -> list[dict]:
        result = self._collection.get(
            where={"doc_id": doc_id},
            include=["documents", "metadatas"],
        )
        return [
            {"chunk_id": cid, "document": doc, "metadata": meta}
            for cid, doc, meta in zip(
                result["ids"], result["documents"], result["metadatas"]
            )
        ]

    def delete_by_doc_id(self, doc_id: str) -> None:
        self._collection.delete(where={"doc_id": doc_id})

    def get_all_chunks_global(self) -> list[dict]:
        """BM25 인덱스 빌드용 — 필터 없이 전체 청크 반환"""
        result = self._collection.get(include=["documents", "metadatas"])
        return [
            {"chunk_id": cid, "document": doc, "metadata": meta}
            for cid, doc, meta in zip(
                result["ids"], result["documents"], result["metadatas"]
            )
        ]

    def count_chunks(self, doc_id: str) -> int:
        result = self._collection.get(where={"doc_id": doc_id}, include=[])
        return len(result["ids"])
