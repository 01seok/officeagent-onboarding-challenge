import uuid
from dataclasses import dataclass


@dataclass
class Chunk:
    chunk_id: str
    doc_id: str
    chunk_index: int
    text: str


class RecursiveTextChunker:
    """
    문단 → 문장 → 어절 순으로 자연스러운 경계를 찾아 분할.
    chunk_size=512, overlap=50으로 문맥 연속성 보장.
    """

    SEPARATORS = ["\n\n", "\n", ". ", "。", " ", ""]
    CHUNK_SIZE = 512
    CHUNK_OVERLAP = 50

    def chunk(self, text: str, doc_id: str) -> list[Chunk]:
        pieces = self._split(text, self.SEPARATORS[0], self.SEPARATORS[1:])
        chunks: list[Chunk] = []
        current = ""
        index = 0

        for piece in pieces:
            if len(current) + len(piece) <= self.CHUNK_SIZE:
                current += piece
            else:
                if current.strip():
                    chunks.append(self._make_chunk(current, doc_id, index))
                    index += 1
                    # overlap: 앞 청크 끝 CHUNK_OVERLAP 글자를 다음 청크 시작에 이어붙임
                    current = current[-self.CHUNK_OVERLAP :] + piece
                else:
                    # 단일 piece가 CHUNK_SIZE 초과 시 강제 분할
                    chunks.append(self._make_chunk(piece[: self.CHUNK_SIZE], doc_id, index))
                    index += 1
                    current = piece[self.CHUNK_SIZE - self.CHUNK_OVERLAP :]

        if current.strip():
            chunks.append(self._make_chunk(current, doc_id, index))

        return chunks

    def _split(self, text: str, sep: str, remaining: list[str]) -> list[str]:
        if not sep:
            return list(text)

        parts = text.split(sep)
        result = []
        for i, part in enumerate(parts):
            if part:
                # 현재 조각이 CHUNK_SIZE보다 크면 다음 separator로 재귀 분할
                if len(part) > self.CHUNK_SIZE and remaining:
                    result.extend(self._split(part, remaining[0], remaining[1:]))
                else:
                    result.append(part)
            if i < len(parts) - 1:
                result.append(sep)
        return result

    def _make_chunk(self, text: str, doc_id: str, index: int) -> Chunk:
        return Chunk(
            chunk_id=str(uuid.uuid4()),
            doc_id=doc_id,
            chunk_index=index,
            text=text.strip(),
        )
