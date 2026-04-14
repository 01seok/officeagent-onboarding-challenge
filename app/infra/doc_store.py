import json
import os
from threading import Lock


class DocumentStore:
    """문서 메타데이터를 JSON 파일로 관리하는 경량 저장소 (SQLite 대체)"""

    def __init__(self, path: str):
        self._path = path
        self._lock = Lock()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        if not os.path.exists(path):
            self._write({})

    def save(self, doc: dict) -> None:
        with self._lock:
            data = self._read()
            data[doc["doc_id"]] = doc
            self._write(data)

    def get(self, doc_id: str) -> dict | None:
        # 쓰기 도중 불완전한 JSON 읽기 방지
        with self._lock:
            return self._read().get(doc_id)

    def list_all(self) -> list[dict]:
        # 쓰기 도중 불완전한 JSON 읽기 방지
        with self._lock:
            return list(self._read().values())

    def update(self, doc_id: str, **kwargs) -> None:
        with self._lock:
            data = self._read()
            if doc_id in data:
                data[doc_id].update(kwargs)
                self._write(data)

    def delete(self, doc_id: str) -> None:
        with self._lock:
            data = self._read()
            data.pop(doc_id, None)
            self._write(data)

    def exists(self, doc_id: str) -> bool:
        # 쓰기 도중 불완전한 JSON 읽기 방지
        with self._lock:
            return doc_id in self._read()

    def _read(self) -> dict:
        with open(self._path, "r", encoding="utf-8") as f:
            return json.load(f)

    def _write(self, data: dict) -> None:
        with open(self._path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
