import logging
from collections.abc import Iterable

from fastapi import Request

from app.api.query.domain import ChunkResult

# 애플리케이션 로그도 uvicorn error 로거를 타도록 맞춰 journald에서 바로 보기 쉽게 함
audit_logger = logging.getLogger("uvicorn.error")


# 질문/답변은 한 줄 로그로 보이게 줄바꿈과 과도한 길이를 정리
def shrink_text(text: str | None, limit: int = 240) -> str:
    normalized = " ".join((text or "").split())
    if len(normalized) <= limit:
        return normalized
    return normalized[: limit - 3] + "..."


# nginx 뒤에 있으므로 x-forwarded-for를 먼저 보기
def client_ip(request: Request) -> str:
    forwarded_for = (request.headers.get("x-forwarded-for") or "").split(",")[0].strip()
    if forwarded_for:
        return forwarded_for
    if request.client is not None and request.client.host:
        return request.client.host
    return "unknown"


# 출처는 파일명과 청크 번호만 요약해서 남겨도 원인 추적에는 충분함
def summarize_sources(chunks: Iterable[ChunkResult], limit: int = 3) -> str:
    items = [
        f"{(chunk.filename or chunk.doc_id or 'unknown')}#{chunk.chunk_index}"
        for chunk in chunks
    ]
    if not items:
        return "-"
    if len(items) <= limit:
        return ", ".join(items)
    preview = ", ".join(items[:limit])
    return f"{preview} (+{len(items) - limit})"
