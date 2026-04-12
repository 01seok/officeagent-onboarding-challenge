from dependency_injector.wiring import Provide, inject
from fastapi import APIRouter, Depends

from app.api.query.schema import QueryRequest, QueryResponse, SourceItem
from app.api.query.service import QueryService
from app.common.exception.app_exception import AppException
from app.common.exception.error_code import ErrorCode
from app.common.response.base_response import BaseResponse
from app.container import Container

router = APIRouter(prefix="/api/v1/query", tags=["query"])


@router.post("", response_model=BaseResponse[QueryResponse])
@inject
async def query_documents(
    request: QueryRequest,
    service: QueryService = Depends(Provide[Container.query_service]),
):
    results = await service.search(request.question, request.top_k, request.doc_id)

    if not results:
        raise AppException(ErrorCode.NO_RELEVANT_CONTENT)

    return BaseResponse.ok(
        QueryResponse(
            question=request.question,
            sources=[
                SourceItem(
                    doc_id=r.doc_id,
                    filename=r.filename,
                    chunk_index=r.chunk_index,
                    text=r.text,
                    score=r.score,
                )
                for r in results
            ],
        )
    )