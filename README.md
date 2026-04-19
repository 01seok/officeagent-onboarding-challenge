# OfficeAgent : Document Q&A API

문서를 업로드하면 내용을 분석하고, 사용자 질문에 대해 **문서 근거 기반 답변**을 생성하는 RAG 파이프라인 REST API

지란지교 백엔드 온보딩 과제 구현물입니다. 설계 의도와 기술 선택 근거는 [`ARCHITECTURE.md`](./ARCHITECTURE.md), 프롬프트 설계 전략은 [`PROMPT_DESIGN.md`](./PROMPT_DESIGN.md)에 있습니다.

---

## 빠른 실행

```bash
brew install redis
npm install -g @openai/codex
python3.13 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
redis-server --daemonize yes
uvicorn app.main:app --port 8000
```

- 서버 주소: `http://127.0.0.1:8000`
- 확인: `curl http://127.0.0.1:8000/health`
- 데모 화면: `http://127.0.0.1:8000/demo`
- 로컬 검증은 `python3.13`으로 진행했고, Docker 이미지는 `python:3.12-slim` 기준입니다.
- LLM 호출을 쓰려면 로컬 `~/.codex`에 Codex 인증이 설정되어 있어야 합니다.
- macOS 환경에서는 `localhost`가 IPv6(`::1`)로 해석될 수 있어, 로컬 접속 주소는 `127.0.0.1` 기준으로 안내합니다.

---

## 면접용 데모 UI

- 기본 실행: 백엔드 서버를 띄운 뒤 `http://127.0.0.1:8000/demo`로 접속하면 됩니다.
- UI 개발용: 백엔드를 `8000` 포트로 띄운 뒤 저장소 루트에서 `npm install`, `npm run dev`
- UI 재빌드: 저장소 루트에서 `npm run build`
- 데모 UI 소스는 `demo-ui/`, FastAPI가 실제로 서빙하는 빌드 결과는 `app/demo_assets/`에 있습니다.

---

## 평가용 추가 확인

선택적으로 아래 항목까지 확인하면 업로드부터 질의응답까지 한 번에 검증할 수 있습니다.

### Docker 실행 (선택)

```bash
docker compose up -d
```

- 첫 빌드는 Python 의존성 + 임베딩 모델 다운로드 때문에 시간이 걸릴 수 있습니다.
- 기동 확인: `curl http://localhost:8000/health`
- Docker 질의까지 확인하려면 호스트 `~/.codex`에 인증이 되어 있어야 합니다.
- 컨테이너는 호스트 `~/.codex`에서 인증/설정만 읽고, 세션/캐시는 Docker 볼륨에 기록합니다.

### API 확인 (선택)

```bash
# 1. 문서 업로드
curl -X POST http://localhost:8000/api/v1/documents \
  -F "file=@sample-docs/company-policy.txt"

# 2. 질의
curl -X POST http://localhost:8000/api/v1/query \
  -H "Content-Type: application/json" \
  -d '{"question": "연차는 어떻게 발생하나요?", "top_k": 5}'
```

---

## 주요 기능

| 기능 | 설명 |
|------|------|
| 문서 수집 | PDF / TXT / Markdown 업로드 → 청킹 → 임베딩 → ChromaDB 저장 |
| 하이브리드 검색 | BM25 키워드 검색 + Vector 의미 검색을 RRF로 융합 |
| LLM 답변 생성 | Codex CLI 기반, 출처(Source) 추적 + 할루시네이션 억제 |
| 스트리밍 응답 | SSE (Server-Sent Events) 엔드포인트 제공 |
| 2-Layer 캐싱 | L1 Exact (SHA256 키) + L2 Semantic (임베딩 유사도) |
| 캐시 무효화 | 문서 삭제 시 역인덱스 기반 정확한 일괄 무효화 |

---

## API 명세

### Document API

```
POST   /api/v1/documents          # 문서 업로드 (multipart/form-data)
GET    /api/v1/documents          # 업로드된 문서 목록
DELETE /api/v1/documents/{doc_id} # 문서 삭제 + 연관 캐시 무효화
```

### Query API

```
POST /api/v1/query                # RAG 질의응답 (JSON 응답)
POST /api/v1/query/stream         # SSE 스트리밍 응답
```

응답 예시:

```json
{
  "success": true,
  "code": 200,
  "data": {
    "question": "연차는 어떻게 발생하나요?",
    "answer": "연차 휴가는 근속 기간에 따라 다르게 발생합니다...",
    "has_relevant_content": true,
    "sources": [
      {
        "doc_id": "...",
        "filename": "company-policy.txt",
        "chunk_index": 0,
        "text": "...",
        "score": 0.011
      }
    ],
    "cache_hit": false
  }
}
```

---

## 디렉토리 구조

```
app/
├── main.py                      # FastAPI 앱, lifespan, 예외 핸들러
├── container.py                 # DI 컨테이너 (dependency-injector)
├── settings.py                  # Pydantic BaseSettings
│
├── api/                         # 도메인별 비즈니스 로직
│   ├── document/                # 문서 도메인 (router/service/repository)
│   └── query/                   # 질의응답 도메인
│
├── common/                      # 공통 모듈
│   ├── exception/               # AppException, ErrorCode
│   ├── response/                # BaseResponse[T]
│   └── middleware/
│
└── infra/                       # 인프라 어댑터 (서비스 계층에서 직접 import 금지)
    ├── chroma.py                # ChromaDB 클라이언트
    ├── cache.py                 # Redis 2-Layer 캐시
    ├── embedding.py             # multilingual-e5-small (384차원)
    ├── llm.py                   # Codex CLI 래퍼
    ├── bm25.py                  # BM25 키워드 검색
    ├── chunker.py               # 재귀 청킹
    ├── parser.py                # PDF/TXT/MD 파서
    └── doc_store.py             # 문서 메타데이터 저장
```

---

## 기술 스택 (요약)

| 영역 | 기술 | 비고 |
|------|------|------|
| 언어 / 프레임워크 | Python 3.12 + FastAPI | async/await 네이티브 |
| 벡터 DB | ChromaDB | 별도 서버 불필요, 파일 기반 영속화 |
| 캐시 DB | Redis 7 | L1 Exact + L2 Semantic |
| 임베딩 | `intfloat/multilingual-e5-small` | 384차원, 한국어 지원 |
| BM25 | rank-bm25 | 순수 Python, 추가 인프라 불필요 |
| LLM | `@openai/codex` | ChatGPT/Codex 인증 기반 |
| DI | dependency-injector | 생명주기 명시적 구분 |

각 기술 선택 근거와 대안 검토는 [`ARCHITECTURE.md`](./ARCHITECTURE.md)를 참고하세요.

---

## 통합 테스트 결과

로컬 환경 end-to-end 검증 완료 항목:

| 검증 항목 | 결과 |
|---|---|
| 서버 기동 (`/health`) | 통과 |
| TXT / Markdown 업로드 | 통과 |
| RAG 질의응답 + 출처 반환 | 통과 |
| L1 Exact 캐시 히트 (동일 질문) | 통과 |
| L2 Semantic 캐시 히트 (유사 질문: "연차는 어떻게 발생하나요?" ↔ "연차가 어떻게 생기나요?") | 통과 |
| 근거 없는 질문 환각 억제 (`has_relevant_content: false`) | 통과 |
| 문서 삭제 시 Redis 캐시 + `__all__` 역인덱스 일괄 무효화 | 통과 |
| SSE 스트리밍 엔드포인트 | 통과 |

---

## 과제 필수 산출물

- [`README.md`](./README.md) : 실행 방법 + 개요
- [`ARCHITECTURE.md`](./ARCHITECTURE.md) : 기술 스택 선택 근거, LLM SDK 통합, 레이어 설계
- [`PROMPT_DESIGN.md`](./PROMPT_DESIGN.md) : 프롬프트 설계 의도와 할루시네이션 억제 전략
