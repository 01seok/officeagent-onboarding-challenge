import hashlib
import json
import logging

import numpy as np
import redis.asyncio as aioredis

logger = logging.getLogger(__name__)


class CacheService:
    # L1 Exact + L2 Semantic 2계층 캐시

    def __init__(self, redis_url: str) -> None:
        # decode_responses=True: bytes 대신 str 반환, json.loads에 바로 사용 가능
        self._redis = aioredis.from_url(redis_url, decode_responses=True)

    # 키 생성 헬퍼
    @staticmethod
    def _exact_key(question: str, doc_id: str | None) -> str:
        # 같은 키가 되어야 하는 것들 정규화
        raw = question.strip().lower() + ":" + (doc_id or "")
        digest = hashlib.sha256(raw.encode()).hexdigest()
        return f"cache:exact:{digest}"

    @staticmethod
    def _sem_key(doc_id: str | None, exact_key: str) -> str:
        suffix = exact_key[-8:]
        scope = doc_id or "all"
        return f"cache:sem:{scope}:{suffix}"

    @staticmethod
    def _doc_index_key(doc_id: str) -> str:
        return f"doc_cache_index:{doc_id}"

    # Layer 1, 완전 일치하는가 ?
    async def get_exact(self, question: str, doc_id: str | None) -> dict | None:
        try:
            key = self._exact_key(question, doc_id)
            raw = await self._redis.get(key)
            if raw is None:
                return None
            return json.loads(raw)
        except Exception as e:
            logger.warning("exact cache get failed: %s", e)
            return None

    async def set_exact(self, question: str, doc_id: str | None, data: dict) -> None:
        try:
            key = self._exact_key(question, doc_id)
            # 24시간 캐싱
            await self._redis.setex(key, 86400, json.dumps(data, ensure_ascii=False))
            # doc_id가 있으면 역인덱스에 등록, 문서 삭제 시 연관 캐시 일괄 무효화용
            if doc_id:
                await self._redis.sadd(self._doc_index_key(doc_id), key)
        except Exception as e:
            logger.warning("exact cache set failed: %s", e)

    # Layer 2, 임베딩 유사도 비교

    async def get_semantic(
        self,
        embedding: list[float],
        doc_id: str | None,
        threshold: float = 0.92,
    ) -> dict | None:
        try:
            scope = doc_id or "all"
            pattern = f"cache:sem:{scope}:*"
            keys = await self._redis.keys(pattern)
            if not keys:
                return None

            query_vec = np.array(embedding)

            for key in keys:
                raw = await self._redis.get(key)
                if raw is None:
                    continue
                entry = json.loads(raw)
                stored_vec = np.array(entry["embedding"])
                # normalize_embeddings=True로 저장된 단위벡터, 내적 = 코사인 유사도
                similarity = float(np.dot(query_vec, stored_vec))
                if similarity >= threshold:
                    return entry["data"]

            return None
        except Exception as e:
            logger.warning("semantic cache get failed: %s", e)
            return None

    async def set_semantic(
        self,
        embedding: list[float],
        doc_id: str | None,
        exact_key: str,
        data: dict,
    ) -> None:
        try:
            key = self._sem_key(doc_id, exact_key)
            payload = json.dumps(
                {"embedding": embedding, "data": data},
                ensure_ascii=False,
            )
            # 48시간 캐싱
            await self._redis.setex(key, 172800, payload)
            # doc_id가 있으면 역인덱스에 등록
            if doc_id:
                await self._redis.sadd(self._doc_index_key(doc_id), key)
        except Exception as e:
            logger.warning("semantic cache set failed: %s", e)

    # 캐시 무효화
    async def invalidate(self, doc_id: str) -> None:
        # 문서 삭제/수정 시 연관된 layer1, layer2 캐시를 일괄 제거
        try:
            index_key = self._doc_index_key(doc_id)
            cache_keys = await self._redis.smembers(index_key)
            if cache_keys:
                pipe = self._redis.pipeline()
                for k in cache_keys:
                    pipe.delete(k)
                pipe.delete(index_key)
                await pipe.execute()
            else:
                # 역인덱스 자체만 남아 있을 수 있으므로 정리
                await self._redis.delete(index_key)
        except Exception as e:
            logger.warning("cache invalidate failed: %s", e)

    # 연결 종료
    async def close(self) -> None:
        try:
            await self._redis.aclose()
        except Exception as e:
            logger.warning("redis close failed: %s", e)
