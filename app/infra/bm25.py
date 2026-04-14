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

    # BM25 점수 상위 n_results 청크 반환, 인덱스가 없으면 빈 리스트
    def search(self, query: str, n_results: int, doc_id: str | None = None) -> list[dict]:
        with self._lock:
            need_rebuild = self._dirty
            if need_rebuild:
                self._dirty = False  # 플래그 먼저 내림

        if need_rebuild:
            # Lock 밖에서 ChromaDB 조회 + 인덱스 빌드 (블로킹 시간 최소화)
            chunks = self._chroma.get_all_chunks_global()
            if chunks:
                corpus = [c["document"].split() for c in chunks]
                new_index = BM25Okapi(corpus)
            else:
                new_index = None
                chunks = []
            # 완성된 인덱스를 atomic하게 교체
            with self._lock:
                self._index = new_index
                self._chunks = chunks

        with self._lock:
            if self._index is None or not self._chunks:
                return []

            tokens = query.split()
            scores = self._index.get_scores(tokens)

            scored = sorted(
                zip(scores, self._chunks),
                key=lambda x: x[0],
                reverse=True,
            )

            results = []
            for score, chunk in scored:
                if score <= 0:
                    break
                # doc_id 필터를 검색 시점에 처리, 후처리 대비 후보 수 보장
                if doc_id and chunk["metadata"].get("doc_id") != doc_id:
                    continue
                results.append({**chunk, "score": float(score)})
                if len(results) >= n_results:
                    break

            return results