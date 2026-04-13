from dependency_injector.wiring import Provide, inject
from fastapi import APIRouter, Depends

from app.api.query.schema import QueryRequest, QueryResponse, SourceItem
from app.api.query.service import QueryService
from app.common.response.base_response import BaseResponse
from app.container import Container

router = APIRouter(prefix="/api/v1/query", tags=["query"])


@router.post("", response_model=BaseResponse[QueryResponse])
@inject
async def query_documents(
    request: QueryRequest,
    service: QueryService = Depends(Provide[Container.query_service]),
):
    # 하이브리드 검색 -> LLM 답변 생성 (예외는 service에서 처리)
    answer, has_relevant_content, chunks = await service.answer(
        request.question, request.top_k, request.doc_id
    )

    return BaseResponse.ok(
        QueryResponse(
            question=request.question,
            answer=answer,
            has_relevant_content=has_relevant_content,
            
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