from time import perf_counter
from uuid import uuid4

from dependency_injector.wiring import Provide, inject
from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse

from app.api.query.audit import audit_logger, client_ip, shrink_text, summarize_sources
from app.api.query.schema import QueryRequest, QueryResponse, SourceItem
from app.api.query.service import QueryService
from app.common.response.base_response import BaseResponse
from app.container import Container

router = APIRouter(prefix="/api/v1/query", tags=["query"])


@router.post("", response_model=BaseResponse[QueryResponse])
@inject
async def query_documents(
    payload: QueryRequest,
    http_request: Request,
    service: QueryService = Depends(Provide[Container.query_service]),
):
    audit_id = uuid4().hex[:8]
    request_ip = client_ip(http_request)
    started = perf_counter()

    audit_logger.info(
        "query_request audit_id=%s ip=%s doc_id=%s top_k=%s question=\"%s\"",
        audit_id,
        request_ip,
        payload.doc_id or "-",
        payload.top_k,
        shrink_text(payload.question, limit=300),
    )

    try:
        # 하이브리드 검색 -> LLM 답변 생성 (예외는 service에서 처리)
        answer, has_relevant_content, chunks, cache_hit = await service.answer(
            payload.question,
            payload.top_k,
            payload.doc_id,
            audit_id=audit_id,
        )
    except Exception:
        audit_logger.exception(
            "query_error audit_id=%s ip=%s doc_id=%s top_k=%s duration_ms=%d question=\"%s\"",
            audit_id,
            request_ip,
            payload.doc_id or "-",
            payload.top_k,
            int((perf_counter() - started) * 1000),
            shrink_text(payload.question, limit=300),
        )
        raise

    audit_logger.info(
        "query_response audit_id=%s ip=%s doc_id=%s duration_ms=%d cache_hit=%s has_relevant_content=%s source_count=%s sources=\"%s\" answer=\"%s\"",
        audit_id,
        request_ip,
        payload.doc_id or "-",
        int((perf_counter() - started) * 1000),
        cache_hit,
        has_relevant_content,
        len(chunks),
        summarize_sources(chunks),
        shrink_text(answer, limit=320),
    )

    return BaseResponse.ok(
        QueryResponse(
            question=payload.question,
            answer=answer,
            has_relevant_content=has_relevant_content,
            cache_hit=cache_hit,

            # 검색된 청크를 출처 형태로 변환하기
            sources=[
                SourceItem(
                    doc_id=r.doc_id,
                    filename=r.filename,
                    chunk_index=r.chunk_index,
                    text=r.text,
                    score=r.score,
                )
                for r in chunks
            ],
        )
    )
    
@router.post("/stream")
@inject
async def stream_query(
    payload: QueryRequest,
    http_request: Request,
    service: QueryService = Depends(Provide[Container.query_service]),
):
    audit_id = uuid4().hex[:8]
    request_ip = client_ip(http_request)
    started = perf_counter()

    audit_logger.info(
        "query_stream_request audit_id=%s ip=%s doc_id=%s top_k=%s question=\"%s\"",
        audit_id,
        request_ip,
        payload.doc_id or "-",
        payload.top_k,
        shrink_text(payload.question, limit=300),
    )

    async def event_generator():
        answer_parts: list[str] = []
        token_count = 0
        try:
            async for token in service.answer_stream(
                payload.question,
                payload.top_k,
                payload.doc_id,
                audit_id=audit_id,
            ):
                answer_parts.append(token)
                token_count += 1
                # SSE 표준 포맷, "data: {내용}\n\n" 형식으로 전달
                yield f"data: {token}\n\n"
        except Exception:
            audit_logger.exception(
                "query_stream_error audit_id=%s ip=%s doc_id=%s top_k=%s duration_ms=%d question=\"%s\"",
                audit_id,
                request_ip,
                payload.doc_id or "-",
                payload.top_k,
                int((perf_counter() - started) * 1000),
                shrink_text(payload.question, limit=300),
            )
            raise
        else:
            answer_text = "".join(answer_parts)
            audit_logger.info(
                "query_stream_done audit_id=%s ip=%s doc_id=%s duration_ms=%d token_count=%s answer=\"%s\"",
                audit_id,
                request_ip,
                payload.doc_id or "-",
                int((perf_counter() - started) * 1000),
                token_count,
                shrink_text(answer_text, limit=320),
            )

    # text/event-stream, SSE 표준, 브라우저/Postman 모두 스트리밍으로 인식
    return StreamingResponse(event_generator(), media_type="text/event-stream")
