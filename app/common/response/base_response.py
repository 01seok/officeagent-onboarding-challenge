from typing import Generic, Optional, TypeVar

from pydantic import BaseModel

T = TypeVar("T")


class BaseResponse(BaseModel, Generic[T]):
    success: bool
    code: int
    message: str
    data: Optional[T] = None
    cache_hit: bool = False

    @classmethod
    def ok(cls, data: T, cache_hit: bool = False) -> "BaseResponse[T]":
        return cls(success=True, code=200, message="요청에 성공했습니다.", data=data, cache_hit=cache_hit)

    @classmethod
    def created(cls, data: T) -> "BaseResponse[T]":
        return cls(success=True, code=201, message="생성에 성공했습니다.", data=data)

    @classmethod
    def error(cls, error_code: "ErrorCode") -> "BaseResponse[None]":
        from app.common.exception.error_code import ErrorCode
        return cls(success=False, code=error_code.code, message=error_code.message)
