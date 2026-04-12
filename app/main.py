from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.container import Container
from app.settings import settings
from app.common.exception.app_exception import AppException
from app.common.response.base_response import BaseResponse


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 시작 시 DI 컨테이너 설정 주입
    container = app.container
    container.config.chroma_persist_dir.from_value(settings.CHROMA_PERSIST_DIR)
    container.config.doc_store_path.from_value(settings.DOC_STORE_PATH)
    container.config.embedding_model.from_value(settings.EMBEDDING_MODEL)
    yield


app = FastAPI(title="OfficeAgent Document Q&A API", lifespan=lifespan)

container = Container()
app.container = container

# 라우터 등록
from app.api.document.router import router as document_router
app.include_router(document_router)


@app.exception_handler(AppException)
async def app_exception_handler(request: Request, exc: AppException):
    return JSONResponse(
        status_code=exc.error_code.http_status.value,
        content=BaseResponse.error(exc.error_code).model_dump(),
    )


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    from app.common.exception.error_code import ErrorCode
    return JSONResponse(
        status_code=500,
        content=BaseResponse.error(ErrorCode.SERVER_ERROR).model_dump(),
    )


@app.get("/health")
async def health():
    return BaseResponse.ok({"status": "ok"})
