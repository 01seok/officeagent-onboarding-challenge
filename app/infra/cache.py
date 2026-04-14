import hashlib
import json
import logging

import numpy as np
import redis.asyncio as aioredis

logger = logging.getLogger(__name__)


_INDEX_TTL = 259200  # 72h, 인덱스 Set TTL (L2 최대 캐시 48h보다 길게 설정)


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
    def _doc_index_key(doc_id: str | None) -> str:
        # doc_id 없는 전체 문서 대상 캐시는 __all__ 키로 묶어서 관리
        return f"doc_cache_index:{doc_id or '__all__'}"

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
            # 역인덱스 등록 + TTL 갱신. 만료된 캐시 키가 인덱스에 계속 쌓이는 것을 방지
            index_key = self._doc_index_key(doc_id)
            pipe = self._redis.pipeline()
            pipe.sadd(index_key, key)
            pipe.expire(index_key, _INDEX_TTL)
            await pipe.execute()
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
            # 역인덱스 등록 + TTL 갱신. 만료된 캐시 키가 인덱스에 계속 쌓이는 것을 방지
            index_key = self._doc_index_key(doc_id)
            pipe = self._redis.pipeline()
            pipe.sadd(index_key, key)
            pipe.expire(index_key, _INDEX_TTL)
            await pipe.execute()
        except Exception as e:
            logger.warning("semantic cache set failed: %s", e)

    # 캐시 무효화
    async def invalidate(self, doc_id: str) -> None:
        # 문서 삭제/수정 시 해당 doc_id 캐시 + 전체 문서 대상(__all__) 캐시 일괄 제거
        try:
            keys_to_clear = [self._doc_index_key(doc_id), self._doc_index_key(None)]
            pipe = self._redis.pipeline()
            for index_key in keys_to_clear:
                cache_keys = await self._redis.smembers(index_key)
                for k in cache_keys:
                    pipe.delete(k)
                pipe.delete(index_key)
            await pipe.execute()
        except Exception as e:
            logger.warning("cache invalidate failed: %s", e)

    # 연결 종료
    async def close(self) -> None:
        try:
            await self._redis.aclose()
        except Exception as e:
            logger.warning("redis close failed: %s", e)
