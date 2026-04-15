# ARCHITECTURE

기술 스택 선택 근거와 설계 의도를 정리한 문서. 이 프로젝트는 채용 과제이지만 실제 서비스에서도 동작할 수 있는 수준의 **안정성과 성능**을 목표로 구현했습니다. 구체적으로는 아래 네 가지 원칙을 설계 전반에 적용했습니다.

1. **결함 격리**: 외부 의존성의 일부 장애가 서비스 전체 장애로 번지지 않게 차단
2. **성능 최적화**: 불필요한 연산 / 네트워크 호출 제거, 병렬화, 캐싱 레이어 도입
3. **레이어 분리**: 도메인 로직과 인프라 어댑터를 명확히 분리해 교체 가능성 확보
4. **관측 가능성**: 예외 범위 한정, 경로별 로그 남기기, 트레이드오프의 명시적 문서화

---

## 1. 기술 스택 선택 근거

### 언어 / 프레임워크: Python 3.12 + FastAPI

임베딩 / 벡터 DB 생태계가 가장 잘 갖춰진 언어가 Python이고, FastAPI는 async/await 네이티브라 LLM 호출처럼 수 초 단위의 I/O 바운드 작업에서 스레드 점유 없이 높은 동시성을 낼 수 있어 선택했습니다. 이전 프로젝트에서 구축했던 **DI 중심의 레이어 설계**, **커스텀 예외 체계**, **통일된 응답 래퍼** 패턴을 FastAPI + `dependency-injector` 조합으로 그대로 이식해 구조적 일관성을 확보했습니다.

### 벡터 DB: ChromaDB

- 별도 서버 불필요, 파일 기반 영속화 (`./data/chroma`)
- 메타데이터 필터링(`where` 절)을 내장 지원 → `doc_id` 기반 특정 문서 한정 검색을 쿼리 한 번으로 처리
- HNSW 인덱싱 내장으로 초기 성능이 충분함
- 대안으로 검토했던 Qdrant, Weaviate, Milvus는 별도 서비스 운영 필요해 과제 배포 조건(한 줄 실행)과 맞지 않음

### 캐시 DB: Redis 7

- L1 Exact Cache (STRING + TTL)
- L2 Semantic Cache (임베딩 포함 JSON + TTL)
- 문서 삭제 시 역인덱스(Set) 기반 일괄 무효화
- 파이프라인(`MULTI/EXEC`)으로 `SADD + EXPIRE` 원자적 처리

자세한 캐시 구조는 아래 **5. 캐싱 전략** 섹션에서 설명합니다.

### 임베딩 모델: `intfloat/multilingual-e5-small` (384차원)

| 고려 사항 | 판단 |
|---|---|
| 언어 지원 | multilingual이라 한국어 사내 문서에 적합 |
| 차원 수 | 384차원, L2 Semantic 캐시 메모리 효율과 업로드 시 배치 임베딩 속도에 유리 |
| 모델 크기 | 약 118MB, 초기 로딩 속도 유리 |
| 라이선스 | MIT |
| prefix 규칙 | `query: {text}` vs `passage: {text}` 분리해 검색 태스크 최적화 |

**왜 large(1024차원)가 아닌가?**

이 과제 도메인은 사내 정책 / 가이드 문서로 수십 ~ 수백 건 규모입니다. 고차원 임베딩의 장점인 "수만 건 중 미세한 의미 차이 구분"이 병목이 될 만한 규모가 아닙니다. 또한 본 설계는 BM25(키워드) + Vector(의미)를 RRF로 융합하는 **하이브리드 검색** 구조라 Vector 단독에 모든 것을 걸지 않아도 되며, 부족한 부분은 BM25가 보완합니다. 설정값 한 줄(`settings.EMBEDDING_MODEL`) 교체로 large로 올릴 수 있게 DI로 분리해뒀습니다.

### LLM: Codex CLI (`@openai/codex`)

과제 허용 조건인 Codex 경로를 선택했고, 별도 서버 SDK 대신 **로컬 `codex exec`를 비동기 subprocess로 호출하는 어댑터**로 통합했습니다. Python 서비스 안에서 직접 LLM을 호출하는 느낌을 유지하면서도, 인증과 모델 실행은 Codex CLI가 담당하게 분리한 구조입니다. 자세한 내용은 아래 **2. LLM SDK 통합 방식** 섹션에 있습니다.

### DI 컨테이너: dependency-injector

FastAPI 기본 `Depends()`는 함수형이라 서비스 계층이 깊어질수록 의존성 선언이 흩어지고 관리가 어렵습니다. `dependency-injector`는 `container.py` 한 파일에 전체 의존성을 선언적으로 모아둘 수 있어 구성이 한눈에 보이고, **Singleton / Factory 생명주기를 명시적으로 구분**할 수 있어 의도가 코드에 그대로 남습니다. 테스트에서 Mock 주입도 container override로 간결하게 처리됩니다.

### BM25: rank-bm25

- 순수 Python 라이브러리라 별도 인프라 불필요
- Elasticsearch 같은 풀 텍스트 엔진 없이 키워드 검색 구현 가능
- 문서 변경 시 lazy rebuild 전략으로 업로드 응답 지연 제거

---

## 2. LLM SDK 통합 방식

### 통합 구조

```
[FastAPI 서비스 레이어]
       ↓ await llm.generate_answer(...)
[LLMService (app/infra/llm.py)]
       ↓ asyncio.create_subprocess_exec(...)
[codex exec --output-schema / --json]
       ↓ stdout / stderr
[Codex CLI (Node.js, @openai/codex)]
       ↓ HTTPS / WebSocket
[OpenAI Codex 런타임]
```

이 구조상 Codex CLI가 실행 환경에 설치되어 있어야 하며, 로컬 실행 시 호스트의 `~/.codex` 디렉토리에 인증 정보가 존재해야 합니다. Docker 환경에서는 이 디렉토리를 읽기 전용으로 마운트합니다.

### 구조화 출력 강제

LLM 응답을 `Pydantic` 모델로 파싱하기 위해 **Codex의 JSON Schema 출력**을 사용했습니다.

```python
class LLMAnswerResult(BaseModel):
    answer: str
    source_indices: list[int]
    has_relevant_content: bool
```

- `source_indices`: LLM이 실제로 참조한 청크 인덱스. 이를 기준으로 응답에 포함할 출처를 재구성합니다.
- `has_relevant_content`: 문서에 근거가 없을 때 LLM이 명시적으로 `false`를 반환하도록 강제. 할루시네이션 억제의 핵심 신호.

`codex exec --output-schema <schema.json>`로 최종 응답을 강제하면, stdout에는 스키마를 만족하는 마지막 메시지만 남습니다. 서비스 레이어는 이 stdout을 그대로 파싱하고, stderr는 진행 로그와 경고만 남깁니다.

### CLI 출력 처리

Codex CLI는 비대화형 모드에서 다음 두 가지 출력을 제공합니다.

1. 일반 모드: stderr에 진행 로그, stdout에 최종 응답
2. `--json` 모드: stdout에 JSONL 이벤트 스트림

이 차이를 이용해
- 일반 질의는 `--output-schema`로 구조화 응답을 받고
- SSE 스트리밍은 `--json` 이벤트 중 `agent_message` 텍스트만 추출해 전송합니다.

구현상으로는 `asyncio.create_subprocess_exec`로 stdout / stderr를 분리 캡처하고, stderr는 잘라서 로깅합니다. CLI 내부 전송 경로 변경이나 진행 로그는 stderr에만 남기고, 서비스는 stdout의 최종 구조화 응답만 파싱하도록 분리했습니다.

### 스트리밍 엔드포인트 (SSE)

`POST /api/v1/query/stream`은 Server-Sent Events 표준(`text/event-stream`)으로 LLM 출력을 전달합니다. Codex의 `--json` 이벤트에서 `agent_message`가 갱신될 때마다 새 텍스트만 추출해 흘려보내도록 구현했습니다. 스트리밍 전용 프롬프트를 별도로 두어 JSON 구조 강제 없이 자연스러운 문장으로 답변하게 했습니다 (자세한 내용은 `PROMPT_DESIGN.md`).

---

## 3. 레이어 분리 설계

### 적용한 설계 원칙

이전 프로젝트에서 체화한 **DI 중심의 레이어 설계** 경험을 이 프로젝트에 이식했습니다. 구체적으로 다음 원칙을 FastAPI 환경에 맞게 적용했습니다.

- **Interface(ABC) + Impl 구조**: `QueryService(ABC)` / `QueryServiceImpl`처럼 계약과 구현을 분리해 Mock 주입과 교체 가능성 확보
- **통일된 응답 래퍼**: `BaseResponse[T]` 제네릭 모델로 모든 응답이 동일 구조(`success / code / message / data / cache_hit`)를 갖도록 함
- **도메인 에러 코드 체계**: `ErrorCode` enum으로 에러 코드와 HTTP 상태 / 메시지를 한 곳에 묶고, 전역 `AppException` 핸들러에서 일관 변환
- **생명주기 명시적 구분**: 인프라는 `Singleton`, 서비스 / 리포지토리는 `Factory`로 DI 컨테이너에 선언
- **비동기 병렬화**: `asyncio.gather()`로 독립적인 I/O 작업 병렬 실행, `BackgroundTasks`로 업로드 후속 처리 비동기화

### 디렉토리 구조 의도

```
app/
├── api/           # 도메인 계층 (비즈니스 로직)
├── common/        # 공통 모듈 (예외, 공통 응답, 미들웨어)
└── infra/         # 인프라 어댑터 (ChromaDB, Redis, 임베딩, LLM, BM25)
```

- **서비스 계층(app/api/**/service.py)은 인프라 구현체를 직접 import하지 않습니다.** DI로 주입받아 사용하므로 인프라 교체 가능성이 확보됩니다. 예컨대 ChromaDB를 Qdrant로 바꾸더라도 `QueryRepository` 서명만 유지하면 서비스 레이어는 변경되지 않습니다.
- **router -> service -> repository** 순으로 의존 방향이 고정되어 있고, 역방향 의존은 없습니다.
- **ABC로 서비스 계약을 명시**: `DocumentService`, `QueryService`를 추상 클래스로 두어 Impl과 분리. 테스트 시 Mock Impl로 교체 가능.

### 통일된 응답 래퍼

```python
class BaseResponse(BaseModel, Generic[T]):
    success: bool
    code: int
    message: str
    data: Optional[T] = None
    cache_hit: bool = False

    @classmethod
    def ok(cls, data: T) -> "BaseResponse[T]": ...
    @classmethod
    def created(cls, data: T) -> "BaseResponse[T]": ...
    @classmethod
    def error(cls, error_code: ErrorCode) -> "BaseResponse[None]": ...
```

모든 응답이 동일한 구조를 가지므로 클라이언트 파싱 로직이 단순해집니다. `cache_hit`은 과제 요구사항인 캐시 히트 여부 전달용입니다.

### 커스텀 예외 체계

```python
class ErrorCode(Enum):
    DOCUMENT_NOT_FOUND   = (-10101, HTTPStatus.NOT_FOUND, "문서를 찾을 수 없습니다.")
    UNSUPPORTED_FORMAT   = (-10103, HTTPStatus.BAD_REQUEST, "지원하지 않는 파일 형식입니다.")
    NO_RELEVANT_CONTENT  = (-10602, HTTPStatus.OK, "문서에서 관련 내용을 찾을 수 없습니다.")
    SERVER_ERROR         = (-10500, HTTPStatus.INTERNAL_SERVER_ERROR, "서버 오류가 발생했습니다.")
```

- 에러 코드 체계는 음수 10000번대로 도메인별 번호 대역 구분
- HTTP 상태 코드와 비즈니스 에러 코드를 분리 (비즈니스 코드가 더 세분화된 상태 표현)
- 전역 예외 핸들러에서 `AppException`을 받아 `BaseResponse.error()`로 변환

---

## 4. RAG 파이프라인 설계

### 전체 흐름

```
[Ingestion]
파일 업로드 → 포맷 감지 → 텍스트 추출 → 청킹 → 배치 임베딩 → ChromaDB 저장
                                                      ↑
                                               메타데이터 (doc_id, chunk_index,
                                                          filename, original_text)

[Query]
질문 입력 → [L1 Exact Cache] → Hit → 즉시 반환
                  ↓ Miss
         쿼리 임베딩 1회 생성
                  ↓
         [L2 Semantic Cache] → Hit → 즉시 반환
                  ↓ Miss
         [병렬] BM25 검색 + Vector 검색 (asyncio.gather, return_exceptions=True)
                  ↓
             RRF 융합 (가중치 0.3 / 0.7, k=60) + 임계값 필터 (0.007)
                  ↓
             Top-K 청크 선택
                  ↓
             LLM 호출 (codex exec)
                  ↓
             응답 파싱 (JSON) + source_indices 매핑
                  ↓
             L1 + L2 fire-and-forget 캐싱 → 응답
```

### 청킹 전략

`RecursiveTextChunker`는 한국어 문서 특성을 고려해 문단 → 문장 → 어절 순으로 분리 시도합니다. `SEPARATORS = ["\n\n", "\n", ". ", "。", " ", ""]`로 우선순위를 뒀고, 청크 크기 512 토큰 / 오버랩 50 토큰으로 문맥 연속성을 보장합니다.

### 하이브리드 검색 (BM25 + Vector + RRF)

```python
bm25_results, vec_results = await asyncio.gather(
    loop.run_in_executor(None, self._bm25.search, query, _BM25_N, doc_id),
    loop.run_in_executor(None, self._chroma.vector_search, query_embedding, _VEC_N, ...),
    return_exceptions=True,
)

if isinstance(bm25_results, Exception):
    bm25_results = []
if isinstance(vec_results, Exception):
    vec_results = []

merged = self._rrf_merge(bm25_results, vec_results)
top = [r for r in merged if r["score"] >= _MIN_SCORE][:top_k]
```

**왜 하이브리드인가?**

- BM25 단독: "일정 관리"를 검색하면 "스케줄 정리" 노트가 안 나오는 키워드 매칭 한계
- Vector 단독: 정확한 고유명사 매칭에 약함, 임베딩 공간의 근접성이 항상 의도와 일치하지 않음
- 두 방식의 장점을 결합하고, 점수 스케일이 다른 두 결과를 공정하게 병합하기 위해 **RRF**(Reciprocal Rank Fusion) 도입

**왜 RRF 가중치 0.3 / 0.7인가?**

사내 문서는 "재택근무 규정"처럼 의미 기반 이해가 중요한 케이스가 많습니다. 키워드 매칭보다 문맥 파악이 사용자 만족도에 더 크게 기여한다는 판단에 Vector에 0.7 가중치를 부여했습니다. 운영 중 튜닝 가능하도록 상수로 분리했습니다.

### 결함 격리 (return_exceptions=True)

검색 인프라 중 하나가 장애여도 나머지로 질의가 계속 진행되도록 설계했습니다.

- Vector 검색 실패 → BM25 결과만으로 RRF 진행 (사실상 BM25 단독 검색과 동일)
- BM25 검색 실패 → Vector 결과만으로 RRF 진행

인프라 한쪽 장애가 서비스 전체 장애로 번지지 않게 격리하는 **결함 격리(Fault Isolation)** 원칙의 구현입니다. 이 패턴은 이전 프로젝트(Second Brain)의 RRF Fallback 경험에서 이식했습니다.

### 환각 억제와 출처 추적

```python
result = await self._llm.generate_answer(question, context)

if not result.has_relevant_content:
    return result.answer, False, []

referenced = [
    chunks[i] for i in dict.fromkeys(result.source_indices)
    if i < len(chunks)
]
sources = referenced
```

- `has_relevant_content=False`면 답변만 반환하고 출처는 빈 리스트 (할루시네이션 신호)
- `source_indices`가 비어있으면 전체 청크를 출처로 내리지 않음 (LLM이 근거를 명시하지 않았다는 뜻)
- `dict.fromkeys`로 중복 제거하면서 순서 유지

---

## 5. 캐싱 전략

### 2-Layer 구조

```
[L1 Exact Cache]
- Key: SHA256(normalize(question) + doc_id)
- TTL: 24시간 (86400s)
- 동일 질문 재호출 시 LLM 호출 없이 즉시 반환

[L2 Semantic Cache]
- Key: cache:sem:{scope}:{suffix}
- Value: { embedding: [...], data: {...} }
- TTL: 48시간 (172800s)
- 쿼리 임베딩과 저장된 임베딩의 코사인 유사도 >= 0.92 일 때 히트
```

**왜 2-Layer인가?**

- L1만 쓰면 "연차는 어떻게 발생하나요?"와 "연차가 어떻게 생기나요?"는 다른 캐시로 취급되어 LLM이 두 번 호출됩니다.
- L2만 쓰면 동일 질문에도 전체 인덱스 스캔이 필요해 매번 연산 비용이 발생합니다.
- 실제 사용 중 검증한 시나리오:
  - 동일 질문 재호출 → L1 Hit (가장 빠름)
  - 표현이 다른 유사 질문 → L2 Hit (LLM 호출 생략)
  - 완전히 새로운 질문 → Miss → LLM 호출

### 임베딩 1회 계산 재사용

```python
cached = await self._cache.get_exact(question, doc_id)
if cached:
    return ..., True

query_embedding = await self._repository.embed_query(question)  # 1회 계산

cached = await self._cache.get_semantic(query_embedding, doc_id)
if cached:
    return ..., True

# 이후 hybrid_search에서도 이 임베딩 재사용
# 캐시 저장 시에도 이 임베딩을 L2 payload로 저장
```

L1 미스 후 단 한 번 계산한 임베딩을 **L2 조회 + L2 저장** 두 곳에서 재사용해 불필요한 모델 호출을 제거했습니다.

### Fire-and-Forget 캐시 저장

```python
exact_key = CacheService._exact_key(question, doc_id)
asyncio.create_task(self._cache.set_exact(question, doc_id, cache_data))
asyncio.create_task(
    self._cache.set_semantic(query_embedding, doc_id, exact_key, cache_data)
)

return result.answer, True, sources, False
```

LLM 답변을 이미 만든 시점에서 Redis 저장 완료를 기다리면 사용자 응답이 지연됩니다. `asyncio.create_task()`로 캐시 저장을 이벤트 루프에 위임하고 즉시 반환하는 구조로 응답 지연 없이 캐싱을 붙였습니다.

캐시 저장 실패 시에도 응답에는 영향이 없고, `cache.py` 내부에서 `try/except + logger.warning`으로 처리됩니다. "캐시는 보조 수단이므로 실패해도 서비스는 계속 동작한다"는 원칙을 지킵니다.

### 캐시 무효화 (역인덱스 기반)

문서가 삭제되면 해당 문서와 연관된 모든 캐시 엔트리를 일괄 제거해야 합니다. 이를 위해 **역인덱스(Reverse Index)** 를 Redis Set으로 유지합니다.

```
doc_cache_index:{doc_id}    → {연관된 cache 키 Set}
doc_cache_index:__all__     → {doc_id 없이 저장된 전체 범위 cache 키 Set}
```

캐시 저장 시 캐시 키 + TTL과 함께 이 역인덱스에도 등록합니다 (`SADD + EXPIRE` 파이프라인).

```python
index_key = self._doc_index_key(doc_id)
pipe = self._redis.pipeline()
pipe.sadd(index_key, key)
pipe.expire(index_key, _INDEX_TTL)  # 72h
await pipe.execute()
```

문서 삭제 시 역인덱스를 조회해 연관 캐시 + 전체 범위 캐시를 함께 무효화합니다.

```python
async def invalidate(self, doc_id: str) -> None:
    keys_to_clear = [self._doc_index_key(doc_id), self._doc_index_key(None)]
    pipe = self._redis.pipeline()
    for index_key in keys_to_clear:
        cache_keys = await self._redis.smembers(index_key)
        for k in cache_keys:
            pipe.delete(k)
        pipe.delete(index_key)
    await pipe.execute()
```

**왜 TTL만이 아니라 역인덱스를 쓰는가?**

TTL 기반 무효화만 쓰면 구현이 단순하지만, 문서 수정 후 최대 24시간 동안 오래된 답변이 반환될 수 있습니다. 정책 / 가이드 문서처럼 **정확성이 중요한** 도메인에서는 부적절합니다. 역인덱스는 쓰기 시 약간의 오버헤드가 있지만 문서 변경 즉시 정확한 캐시 삭제가 가능합니다.

**왜 `__all__` 범위 캐시도 함께 무효화하는가?**

`doc_id=None`인 질의(전체 문서 대상)는 어떤 문서가 삭제되든 결과가 바뀔 수 있습니다. 따라서 doc 단위 인덱스와 `__all__` 인덱스를 함께 정리해야 stale 응답을 막을 수 있습니다.

**왜 인덱스 Set 자체에 TTL을 거는가?**

캐시 키는 24h / 48h TTL로 자동 만료되지만, 역인덱스 Set은 자체 TTL이 없으면 이미 만료된 키가 멤버로 계속 누적됩니다. 인덱스 TTL(72h, L2 최대 TTL보다 길게)을 설정해 Set도 자연스럽게 정리되게 했습니다.

---

## 6. 비동기 처리와 성능 최적화

### 병렬 처리 전략

| 지점 | 기법 | 효과 |
|---|---|---|
| 하이브리드 검색 | `asyncio.gather()` | 응답 시간 = max(t_bm25, t_vector) |
| 청킹 임베딩 | `model.encode(batch)` | 개별 호출 대비 수 배 빠름 |
| 캐시 저장 | `asyncio.create_task()` | 응답 지연 0, 이벤트 루프에서 별도 실행 |
| 역인덱스 일괄 삭제 | Redis Pipeline | 다수 DEL을 1 RTT에 처리 |
| 동기 블로킹 작업 | `run_in_executor` | ChromaDB / BM25 호출을 이벤트 루프에서 분리 |

### 문서 업로드 백그라운드 처리

```python
@router.post("", response_model=BaseResponse[DocumentUploadResponse])
async def upload_document(file, background_tasks, service):
    data = await file.read()
    doc = await service.upload(file)
    background_tasks.add_task(service.process, doc.doc_id, doc.content_type, data)
    return BaseResponse.created(...)  # 즉시 반환
```

파싱 / 청킹 / 임베딩은 파일 크기에 비례해 시간이 걸립니다. `BackgroundTasks`로 처리를 비동기화해 업로드 응답은 즉시 반환하고, 실제 처리는 상태 필드(`status: processing | completed | failed`)로 추적합니다.

### 동시성 제어

`DocumentStore`의 파일 I/O, `BM25Searcher`의 인덱스 rebuild는 멀티 코루틴 환경에서 경쟁 가능성이 있어 `threading.Lock`으로 보호했습니다. rebuild 자체는 Lock 밖으로 빼서 다른 검색 요청을 블로킹하지 않도록 분리했습니다.

---

## 7. 알려진 트레이드오프와 개선 방향

본 프로젝트는 과제 범위 내에서 구현 품질을 우선시했습니다. 아래 항목들은 **인지하고 있지만 의도적으로 과제 범위에서 제외한 것들**이며, 운영 환경 이식 시 검토가 필요합니다.

### 캐시 무효화의 경쟁 조건

`invalidate()`가 `SMEMBERS`로 Set 멤버를 스냅샷하고 `pipe.execute()`로 삭제하는 사이에, fire-and-forget으로 실행 중인 `set_exact` / `set_semantic`이 같은 인덱스에 새 키를 추가할 수 있습니다. 이 경우 새로 추가된 캐시 키는 역인덱스에서 사라지지만 캐시 엔트리는 TTL까지 살아남는 구조가 됩니다.

**현재 판단**: 단일 프로세스 asyncio 환경에서 발생 확률이 낮고, 과제 규모(샘플 문서 2개, 소규모 데모)에서는 실질적 영향이 없습니다.

**운영 이식 시 개선 방향**:
- **버전 키 방식**: 무효화 시 `cache_version:{doc_id}` 값을 증가시키고, 캐시 조회 시 현재 버전과 키의 버전을 비교. 오래된 버전 키는 자연히 무시됨.
- **원자적 인덱스 교체**: `RENAME`으로 기존 Set을 백업 네임스페이스에 옮긴 뒤 멤버를 순회하며 삭제. 새 write는 fresh 인덱스로 유입.

### LLM 호출 timeout / retry 정책

Codex CLI 역시 subprocess 기반 호출이라 Python 서비스가 직접 전송 계층을 제어하지는 않습니다. 따라서 세밀한 재시도보다 **CLI가 제공하는 기본 재시도/전송 전환을 신뢰하고, 서비스는 stdout 파싱과 stderr 관측에 집중**하는 방향으로 설계했습니다.

운영 환경에서는 다음을 추가 고려할 수 있습니다:
- `asyncio.wait_for`로 전체 generator 소비에 wall-clock timeout 적용
- CLI 전송 경로 변경 warning 빈도를 메트릭으로 수집해 CLI 버전 업데이트 시점 판단

### Semantic Cache 조회가 `KEYS` 전체 스캔

`get_semantic`이 `cache:sem:{scope}:*` 패턴의 모든 키를 `KEYS` 명령으로 조회합니다. 캐시 키가 많아지면 Redis 단일 스레드 특성상 latency가 증가합니다.

**현재 판단**: 과제 규모에서 문제가 되지 않고, L2 Semantic Cache의 TTL 48h가 키 수를 자연히 제한합니다.

**운영 이식 시 개선 방향**:
- `SCAN` 기반 점진적 순회로 변경
- 임베딩 검색 특화 DB(Redis Stack VECTOR SEARCH, Qdrant 등)로 L2 이전

---

## 8. 설계 일관성 원칙

프로젝트 전체에서 반복적으로 적용한 원칙입니다.

1. **외부 의존성은 항상 실패할 수 있다고 가정**: ChromaDB, Redis, Codex CLI 모두 예외 처리 경로를 둠. 캐시 실패는 서비스 실패가 아님.
2. **예외는 타입을 좁혀서 잡는다**: `except Exception`은 예상 밖의 오류를 은닉함. 특정 타입만 catch하고 나머지는 전파.
3. **트레이드오프는 코드가 아니라 문서에 기록한다**: 현재 코드의 제약은 주석이 아니라 이 문서(ARCHITECTURE.md)에서 설명. 주석은 시간이 지나면 무너지지만 문서는 유지된다.
4. **인프라 교체 가능성을 확보한다**: 모든 인프라 의존은 DI 주입. 서비스 레이어가 구현체를 직접 import하지 않음.
5. **관측 가능한 오류 처리**: 모든 예외 처리에 logger를 남겨 프로덕션에서 발생 빈도를 추적 가능.
