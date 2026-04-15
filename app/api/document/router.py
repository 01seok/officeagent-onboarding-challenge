from dependency_injector.wiring import Provide, inject
from fastapi import APIRouter, BackgroundTasks, Depends, UploadFile
from pathlib import Path

from app.api.document.schema import DocumentListItem, DocumentUploadResponse
from app.api.document.service import DocumentService
from app.common.exception.app_exception import AppException
from app.common.exception.error_code import ErrorCode
from app.common.response.base_response import BaseResponse
from app.container import Container

router = APIRouter(prefix="/api/v1/documents", tags=["documents"])

ALLOWED_CONTENT_TYPES = {
    "application/pdf",
    "text/plain",
    "text/markdown",
    "text/x-markdown",
}

EXTENSION_TO_CONTENT_TYPE = {
    ".pdf": "application/pdf",
    ".txt": "text/plain",
    ".md": "text/markdown",
    ".markdown": "text/markdown",
}


# 클라이언트 MIME 타입이 유동적이더라도 확장자로 한 번 더 보정
def _normalize_content_type(file: UploadFile) -> str | None:
    filename = (file.filename or "").strip().lower()
    suffix = Path(filename).suffix

    if suffix in EXTENSION_TO_CONTENT_TYPE:
        return EXTENSION_TO_CONTENT_TYPE[suffix]

    content_type = (file.content_type or "").strip().lower()
    if content_type == "text/x-markdown":
        return "text/markdown"
    if content_type in ALLOWED_CONTENT_TYPES:
        return content_type
    return None


@router.post("", response_model=BaseResponse[DocumentUploadResponse])
@inject
async def upload_document(
    file: UploadFile,
    background_tasks: BackgroundTasks,
    service: DocumentService = Depends(Provide[Container.document_service]),
):
    normalized_content_type = _normalize_content_type(file)
    if normalized_content_type is None:
        raise AppException(ErrorCode.UNSUPPORTED_FORMAT)

    data = await file.read()
    doc = await service.upload(file, normalized_content_type)

    # 파싱/청킹/임베딩은 백그라운드에서 처리 (응답은 즉시 반환)
    background_tasks.add_task(service.process, doc.doc_id, doc.content_type, data)

    return BaseResponse.created(
        DocumentUploadResponse(
            doc_id=doc.doc_id,
            filename=doc.filename,
            status=doc.status,
        )
    )


@router.get("", response_model=BaseResponse[list[DocumentListItem]])
@inject
async def list_documents(
    service: DocumentService = Depends(Provide[Container.document_service]),
):
    docs = service.list_documents()
    items = [DocumentListItem(**d) for d in docs]
    return BaseResponse.ok(items)


@router.delete("/{doc_id}", response_model=BaseResponse[None])
@inject
async def delete_document(
    doc_id: str,
    service: DocumentService = Depends(Provide[Container.document_service]),
):
    await service.delete_document(doc_id)
    return BaseResponse.ok(None)
