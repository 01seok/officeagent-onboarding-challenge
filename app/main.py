from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.container import Container
from app.common.exception.app_exception import AppException
from app.common.response.base_response import BaseResponse

app = FastAPI(title="OfficeAgent Document Q&A API")

container = Container()
app.container = container


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
