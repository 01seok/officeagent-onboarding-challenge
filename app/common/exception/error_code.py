from enum import Enum
from http import HTTPStatus


class ErrorCode(Enum):
    # 형식: (code, http_status, message)

    # 문서 관련 (-10100~)
    DOCUMENT_NOT_FOUND   = (-10101, HTTPStatus.NOT_FOUND,            "문서를 찾을 수 없습니다.")
    DOCUMENT_PARSE_ERROR = (-10102, HTTPStatus.BAD_REQUEST,          "문서 파싱에 실패했습니다.")
    UNSUPPORTED_FORMAT   = (-10103, HTTPStatus.BAD_REQUEST,          "지원하지 않는 파일 형식입니다.")

    # 검색 관련 (-10600~)
    SEARCH_FAILED        = (-10601, HTTPStatus.INTERNAL_SERVER_ERROR, "검색 중 오류가 발생했습니다.")
    NO_RELEVANT_CONTENT  = (-10602, HTTPStatus.OK,                   "문서에서 관련 내용을 찾을 수 없습니다.")

    # 캐시 관련 (-10700~)
    CACHE_UNAVAILABLE    = (-10701, HTTPStatus.SERVICE_UNAVAILABLE,  "캐시 서비스를 사용할 수 없습니다.")

    # 서버 에러 (-10500)
    SERVER_ERROR         = (-10500, HTTPStatus.INTERNAL_SERVER_ERROR, "서버 오류가 발생했습니다.")

    def __init__(self, code: int, http_status: HTTPStatus, message: str):
        self.code = code
        self.http_status = http_status
        self.message = message
