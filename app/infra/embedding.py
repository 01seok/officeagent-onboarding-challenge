import asyncio

from sentence_transformers import SentenceTransformer


class EmbeddingService:
    def __init__(self, model_name: str):
        self._model = SentenceTransformer(model_name)

    async def embed_query(self, text: str) -> list[float]:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self._encode, f"query: {text}")

    async def embed_passage(self, text: str) -> list[float]:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self._encode, f"passage: {text}")

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        # 청킹 결과를 한 번에 임베딩 (개별 호출보다 배치가 훨씬 빠름)
        loop = asyncio.get_running_loop()
        prefixed = [f"passage: {t}" for t in texts]
        return await loop.run_in_executor(
            None,
            lambda: self._model.encode(prefixed, normalize_embeddings=True).tolist(),
        )

    def _encode(self, text: str) -> list[float]:
        # normalize_embeddings=True → 코사인 유사도를 단순 내적으로 대체 가능
        return self._model.encode(text, normalize_embeddings=True).tolist()
