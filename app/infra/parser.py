import io
import re

import markdown
import PyPDF2

from app.common.exception.app_exception import AppException
from app.common.exception.error_code import ErrorCode


class DocumentParser:
    def parse(self, content_type: str, data: bytes) -> str:
        match content_type:
            case "application/pdf":
                return self._parse_pdf(data)
            case "text/plain":
                return self._parse_txt(data)
            case "text/markdown" | "text/x-markdown":
                return self._parse_md(data)
            case _:
                raise AppException(ErrorCode.UNSUPPORTED_FORMAT)

    def _parse_pdf(self, data: bytes) -> str:
        reader = PyPDF2.PdfReader(io.BytesIO(data))
        pages = [page.extract_text() or "" for page in reader.pages]
        return "\n".join(pages)

    def _parse_txt(self, data: bytes) -> str:
        return data.decode("utf-8", errors="ignore")

    def _parse_md(self, data: bytes) -> str:
        raw = data.decode("utf-8", errors="ignore")
        html = markdown.markdown(raw)
        # HTML 태그 제거 후 plain text 반환
        return re.sub(r"<[^>]+>", "", html)
