import threading

from rank_bm25 import BM25Okapi

from app.infra.chroma import ChromaClient


class BM25Searcher:
    """
    ChromaDB 전체 청크를 대상으로 BM25 키워드 검색.
    Singleton으로 관리되며, 문서 추가/삭제 시 invalidate() 호출로 재빌드 예약.
    실제 재빌드는 다음 search() 호출 시 지연 실행 (lazy rebuild).
    """

    def __init__(self, chroma: ChromaClient):
        self._chroma = chroma
        self._index: BM25Okapi | None = None
        self._chunks: list[dict] = []
        self._lock = threading.Lock()
        self._dirty = True  # 최초 search 시 자동 빌드

    def invalidate(self) -> None:
        # 문서 추가/삭제 후 호출 > 다음 search 시 재빌드
        with self._lock:
            self._dirty = True

    def _rebuild(self) -> None:
        chunks = self._chroma.get_all_chunks_global()
        if not chunks:
            self._index = None
            self._chunks = []
            return
        corpus = [c["document"].split() for c in chunks]
        self._index = BM25Okapi(corpus)
        self._chunks = chunks

    def search(self, query: str, n_results: int) -> list[dict]:
        # BM25 점수 상위 n_results 청크 반환, 인덱스가 없으면 빈 리스트
        with self._lock:
            if self._dirty:
                self._rebuild()
                self._dirty = False

            if self._index is None or not self._chunks:
                return []

            tokens = query.split()
            scores = self._index.get_scores(tokens)

            scored = sorted(
                zip(scores, self._chunks),
                key=lambda x: x[0],
                reverse=True,
            )[:n_results]

            return [
                {**chunk, "score": float(score)}
                for score, chunk in scored
                if score > 0
            ]
