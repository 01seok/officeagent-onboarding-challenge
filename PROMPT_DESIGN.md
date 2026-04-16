# PROMPT DESIGN

RAG 파이프라인에서 사용하는 프롬프트의 설계 의도와 전략을 정리한 문서. 프롬프트는 단순한 지시문이 아니라 **"LLM이 할루시네이션을 일으키지 않도록 제약하는 계약서"** 라는 관점으로 설계했습니다.

---

## 1. 설계 원칙

이 프로젝트의 프롬프트는 세 가지 원칙 위에서 만들어졌습니다.

1. **근거 강제**: 답변은 반드시 제공된 문서 범위 안에서만 생성되어야 한다. 일반 지식이나 추측이 섞이면 안 된다.
2. **할루시네이션의 명시적 신호화**: 문서에 근거가 없을 때 LLM이 "모르는데 아는 척" 하는 것이 가장 위험하다. 근거 없음을 **구조화된 필드**로 반환하게 해서 다운스트림 로직이 감지할 수 있게 만든다.
3. **파싱 실패도 안전하게 처리**: LLM이 규약을 지키지 않을 가능성은 항상 존재한다. 파싱 실패 시에도 "모른다"로 떨어지는 fallback 경로를 설계한다.

즉, 프롬프트 단 하나에 기대지 않고 **프롬프트 -> 구조화 출력 -> 파싱 검증 -> fallback** 까지 여러 방어선을 연결해 할루시네이션을 억제합니다.

---

## 2. System Prompt 설계

### 일반 응답용 (JSON 구조화)

```
당신은 제공된 문서를 기반으로만 답변하는 Q&A 어시스턴트입니다.
규칙:
  1. 반드시 [Source N] 태그로 표시된 문서 내용만 근거로 사용하세요.
  2. 문서에 관련 내용이 없으면 has_relevant_content를 false로 설정하고,
     answer에 '제공된 문서에서 관련 내용을 찾을 수 없습니다.'라고 작성하세요.
  3. 반드시 아래 JSON 형식으로만 응답하세요. 다른 텍스트는 포함하지 마세요:
  {"answer": "답변 내용", "source_indices": [0, 1], "has_relevant_content": true}
```

### 설계 포인트

**① 역할(role)을 한 줄에 못박음**

첫 문장에서 "제공된 문서를 기반으로만 답변하는" 이라는 범위 제약을 역할 정의와 함께 묶었습니다. LLM은 역할 정의에 따라 응답 스타일이 크게 달라지므로, 역할 자체에 제약을 내장하는 것이 규칙을 3번에 걸쳐 반복하는 것보다 효과적입니다.

**② 규칙을 번호로 나누고 각 규칙이 독립적으로 해석되게 함**

한 문단에 모든 요구사항을 섞으면 LLM이 일부 규칙만 선택적으로 따를 수 있습니다. 번호로 분리해 각 규칙을 병렬적으로 강제했습니다.

**③ "다른 텍스트는 포함하지 마세요" 강조**

LLM은 기본적으로 설명을 덧붙이려는 경향이 있습니다. "코드 앞뒤에 설명하는 LLM 특유의 습관"을 막기 위해 JSON 전용 응답을 명시적으로 요구했습니다. 파싱 단계에서 이를 정규표현식 fallback으로 한 번 더 방어합니다.

**④ JSON 예시를 직접 제시**

규칙으로 형식을 설명하기보다 **실제 출력 예시**를 보여주는 것이 LLM 응답 일관성에 더 효과적입니다. 프롬프트의 마지막 줄에 완성된 JSON 예시를 넣어 구조 학습 비용을 낮췄습니다.

### 스트리밍용 (자연어 응답)

```
당신은 제공된 문서를 기반으로만 답변하는 Q&A 어시스턴트입니다.

규칙:
1. 반드시 [Source N] 태그로 표시된 문서 내용만 근거로 사용하세요.
2. 문서에 관련 내용이 없으면 '제공된 문서에서 관련 내용을 찾을 수 없습니다.'라고 답하세요.
3. 자연스러운 문장으로 간결하게 답변하세요.
```

### 왜 프롬프트를 두 벌로 분리했는가?

스트리밍 엔드포인트(`POST /api/v1/query/stream`)는 **토큰 단위로 클라이언트에 바로 전달**하는 구조입니다. JSON 응답을 토큰 단위로 흘려보내면 클라이언트 입장에서 반쪽짜리 JSON을 계속 받게 되고, 최종적으로 JSON이 완성될 때까지 아무것도 표시할 수 없습니다.

따라서 스트리밍 전용 프롬프트에서는 **JSON 강제를 제거하고 자연어로 답변**하게 해 토큰이 곧바로 의미 있는 텍스트로 전달되도록 설계했습니다. 근거 제약(`[Source N]` 태그만 사용)과 할루시네이션 억제 문구는 그대로 유지해 응답 품질은 일관되게 보장됩니다.

**같은 프롬프트를 공유하고 응답 시점에 분기 처리하지 않은 이유**: 용도별 분리가 장기적으로 유지보수에 유리합니다. 스트리밍 응답 품질을 튜닝할 때 JSON 응답에 영향을 주지 않아야 하고, 그 반대도 마찬가지입니다.

---

## 3. User Prompt 구조

### 실제 생성되는 프롬프트 예시

```
[Source 0] 문서: company-policy.txt
---
입사 1년 미만: 매월 1일 발생
입사 1년 이상: 연 15일 (근속 2년마다 1일 추가, 최대 25일)
---

[Source 1] 문서: development-guide.md
---
모든 PR은 최소 1명의 리뷰어 승인을 받아야 머지 가능
---

질문: 연차는 어떻게 발생하나요?
```

### 설계 포인트

**① `[Source N]` 태그 도입**

RAG 응답의 핵심 요구사항은 **"어떤 문서의 어떤 부분에서 나왔는지"** 를 명확히 추적하는 것입니다. LLM에게 청크를 그냥 나열해 주면 "종합적으로 답변"해버리고 출처를 흐리는 경향이 있습니다.

`[Source 0]`, `[Source 1]` 처럼 **정수 인덱스로 청크에 번호를 매겨** 전달하면 LLM이 `source_indices: [0, 1]` 같은 구조화된 형태로 정확한 출처를 반환할 수 있습니다. 이 인덱스를 서버에서 다시 원본 청크와 매핑해 응답의 `sources` 필드를 구성합니다.

**② 구분자 `---` 로 청크 경계 명확화**

여러 청크가 한 프롬프트에 들어갈 때 청크 경계가 흐려지면 LLM이 실제로는 서로 다른 문서의 내용을 한 청크로 오해할 수 있습니다. `---` 구분자로 시각적 경계를 주고, `[Source N] 문서: {filename}` 헤더로 출처 메타데이터를 명시했습니다.

**③ 질문과 컨텍스트의 위치 분리**

질문을 프롬프트 **마지막**에 배치했습니다. LLM은 긴 컨텍스트를 받았을 때 뒤쪽 내용에 더 높은 가중치를 두는 경향(recency bias)이 있어, 질문이 마지막에 위치하면 응답이 질문에 더 집중됩니다.

### 프롬프트 구성 코드 (app/infra/llm.py)

```python
def _build_prompt(
    self,
    question: str,
    chunks: list[dict],
    instructions: str,
) -> str:
    sources = "\n\n".join(
        f"[Source {i}] 문서: {c['filename']}\n---\n{c['text']}\n---"
        for i, c in enumerate(chunks)
    )
    return f"{instructions}\n\n{sources}\n\n질문: {question}"
```

현재 구현은 일반 응답용 / 스트리밍용 프롬프트를 별도 상수로 유지하되, 실제 Source 블록 조립은 `_build_prompt()` 하나로 공통 처리합니다. 즉, **청크 구조는 일관되게 유지하면서 지시문만 목적별로 바꾸는 구조**입니다.

---

## 4. 구조화 출력과 파싱

### Pydantic 스키마

```python
class LLMAnswerResult(BaseModel):
    answer: str
    source_indices: list[int]
    has_relevant_content: bool
```

- `answer`: 사용자에게 전달할 최종 답변 텍스트
- `source_indices`: LLM이 실제 참조한 청크 인덱스 (UI의 출처 표시에 사용)
- `has_relevant_content`: 문서에 근거가 있는지 여부 (할루시네이션 탐지 플래그)

### 파싱 전략

```python
def _parse_response(self, raw: str) -> LLMAnswerResult:
    # 1단계: 중괄호 블록만 추출 (LLM이 설명문을 앞뒤에 붙이는 경우 방어)
    match = re.search(r"\{.*\}", raw, re.DOTALL)
    if match:
        try:
            return LLMAnswerResult.model_validate_json(match.group())
        except (ValidationError, ValueError):
            pass
    # 2단계: 파싱 실패 시 fallback
    return LLMAnswerResult(
        answer=raw.strip() or "답변을 생성할 수 없습니다.",
        source_indices=[],
        has_relevant_content=False,
    )
```

**단계별 방어**:

1. **정규표현식 추출**: `re.search(r"\{.*\}", raw, re.DOTALL)` 로 중괄호 블록만 뽑아냄. LLM이 "다음은 답변입니다: {...}" 처럼 설명을 덧붙여도 JSON 부분만 파싱 대상이 됨.
2. **Pydantic 검증**: 타입이 맞지 않으면 `ValidationError` 발생 -> except에서 fallback으로 이동
3. **Fallback**: 파싱 실패한 응답은 **`has_relevant_content=False`로 분류**. JSON을 못 뽑아낸 응답을 "근거 있는 답변"으로 인정하지 않아 다운스트림에서 출처 없는 답변이 유저에게 노출되는 것을 막음.

이 파싱 전략은 "LLM이 규칙을 안 지켜도 서비스는 무너지지 않는다" 라는 안정성 원칙의 실제 구현입니다.

---

## 5. 할루시네이션 억제 다중 방어선

RAG 시스템에서 할루시네이션을 하나의 방어 수단으로 막는 것은 불가능합니다. 이 프로젝트는 **네 겹의 방어선**을 설계해 각 단계에서 할루시네이션이 필터링되도록 했습니다.

### 방어선 1: 검색 단계 (Retrieval)

기본 경로는 BM25 + Vector + RRF 하이브리드 검색입니다. **두 검색기가 모두 정상일 때만** RRF 최소 점수 `_MIN_SCORE = 0.007`을 적용해 노이즈를 제거합니다.

```python
if mode is RetrievalMode.HYBRID:
    return [r for r in merged if r["score"] >= _MIN_SCORE][:top_k]
```

다만 운영 환경에서는 한쪽 검색기만 일시적으로 실패할 수 있습니다. 이때도 같은 hybrid cutoff를 그대로 적용하면, 살아남은 검색기 결과가 전부 필터링되어 장애가 곧바로 "관련 문서 없음"처럼 보일 수 있습니다. 현재 구현은 이를 막기 위해 **degraded retrieval 전용 기준**을 둡니다.

```python
fallback_k = min(top_k, _FALLBACK_TOP_N)
return merged[:fallback_k]
```

즉, 한쪽 검색기만 살아 있으면 `_MIN_SCORE` 대신 상위 `min(top_k, 3)`개만 보수적으로 LLM에 전달하고, 최종적으로는 `has_relevant_content`와 `source_indices`로 한 번 더 걸러냅니다. 양쪽 검색기가 모두 실패했을 때만 검색 실패로 처리합니다.

### 방어선 2: 프롬프트 단계 (System / User Prompt)

- System Prompt의 규칙 1번: "반드시 [Source N] 태그로 표시된 문서 내용만 근거로 사용하세요"
- 규칙 2번: "문서에 관련 내용이 없으면 has_relevant_content를 false로 설정"
- 역할 정의에 범위 제약 내장

### 방어선 3: 구조화 응답 단계 (Structured Output)

`has_relevant_content: bool` 필드를 강제해 LLM이 스스로 "근거 없음"을 구조적으로 신호할 수 있게 함. 자연어로 "~~인 것 같습니다" 같은 애매한 응답을 내면 이 필드로 걸러짐.

```python
result = await self._llm.generate_answer(question, context)

if not result.has_relevant_content:
    return result.answer, False, []  # sources 비움
```

### 방어선 4: 출처 매핑 단계 (Source Validation)

LLM이 `source_indices: []` 처럼 비어있는 리스트를 반환했다면 "근거를 명시하지 못한 응답"으로 간주해 전체 청크를 출처로 내리지 않음.

```python
referenced = [
    chunks[i] for i in dict.fromkeys(result.source_indices)
    if i < len(chunks)
]
sources = referenced  # 비어있으면 그대로 빈 리스트
```

또한 LLM이 존재하지 않는 인덱스(`source_indices: [99]`)를 반환하는 경우에도 `i < len(chunks)` 체크로 방어됩니다.

### no-content 응답과 캐시 정책

현재 구현에서 검색 결과가 비었거나, LLM이 `has_relevant_content=False`를 반환했거나, `source_indices`가 비어 있으면 모두 **정상 no-content 응답**으로 처리합니다. 즉, 사용자에게는 "제공된 문서에서 관련 내용을 찾을 수 없습니다."를 반환하고 서비스 레벨 예외로 올리지 않습니다.

대신 캐시 정책은 보수적으로 가져갑니다.

- no-content 응답은 **L1 exact cache만 저장**
- semantic cache는 **근거가 있는 positive 응답**에만 사용
- partial failure 상태에서 나온 positive 응답도 semantic cache로 일반화하지 않음

이렇게 해야 일시적인 retrieval miss나 degraded 결과가 이후 유사 질문들까지 false negative로 퍼지는 것을 막을 수 있습니다.

### 방어선이 함께 작동하는 시나리오

질문: "파이썬으로 웹 크롤러 만드는 방법 알려줘"
문서: 사내 정책 / 가이드 (관련 내용 없음)

1. **방어선 1** (검색): BM25 / Vector 모두 유사도 낮음, 하지만 Top-K는 억지로 뭔가를 반환
2. **방어선 2** (프롬프트): LLM이 "문서에 관련 내용이 없으면 has_relevant_content를 false로" 규칙 인지
3. **방어선 3** (구조화 응답): LLM이 `has_relevant_content: false`, `answer: "제공된 문서에서 관련 내용을 찾을 수 없습니다."` 반환
4. **방어선 4** (출처 매핑): `has_relevant_content=False`이면 sources 비움

최종 응답:
```json
{
  "answer": "제공된 문서에서 관련 내용을 찾을 수 없습니다.",
  "has_relevant_content": false,
  "sources": []
}
```

실제 통합 테스트에서 이 시나리오가 설계대로 동작하는 것을 확인했습니다.

---

## 6. 트레이드오프와 고려사항

### JSON 강제가 응답 품질에 주는 영향

JSON 형식을 강제하면 LLM의 **자유로운 답변 생성 품질**이 미세하게 떨어질 수 있습니다. 특히 긴 답변의 마크다운 서식(불릿, 코드 블록)이 JSON 문자열로 직렬화되면서 탈출 문자가 늘어나 가독성이 떨어지는 경우가 있습니다.

**이 프로젝트의 선택**:
- 일반 응답 (`/query`): JSON 강제로 구조화 출력 확보. 할루시네이션 탐지가 답변 자유도보다 중요.
- 스트리밍 응답 (`/query/stream`): JSON 강제 제거로 자연스러운 답변. 실시간 UX를 위한 트레이드오프.

### source_indices의 LLM 준수율

LLM이 `source_indices`를 정확히 반환하는지 여부는 모델 성능에 의존합니다. 현재 프롬프트는 "어떤 청크를 썼는지 번호로 알려달라"고 명시적으로 요구하지만, LLM이 여러 청크를 종합했을 때 모두를 정확히 열거하지 못할 수 있습니다.

**이에 대한 설계 선택**: `source_indices`가 비어있으면 grounded answer로 인정하지 않고 no-content로 내려서 "정확한 근거 추적"을 우선시함. 향후 튜닝 시 `source_indices`를 반드시 하나 이상 포함하도록 프롬프트를 강화할 수 있음.

### degraded retrieval의 장점과 한계

한쪽 검색기가 실패했을 때 상위 3개 후보를 LLM에 넘기는 방식의 장점은, 일시적 장애 때문에 무조건 빈 검색으로 끝나는 상황을 줄인다는 점입니다. 사용자에게는 여전히 문서 기반 답변만 보여주되, retrieval 자체의 결함이 곧바로 "문서에 관련 내용이 없다"로 오인되지 않게 합니다.

반면 한계도 분명합니다. 단일 검색기 결과만 들어간 degraded mode에서는 hybrid mode보다 문맥 정확도가 떨어질 수 있으므로, **후보 수를 3개로 제한**하고 `has_relevant_content`, `source_indices`, exact-only caching으로 보수적으로 운영합니다. 즉, recall을 약간 보완하되 precision을 무너뜨리지 않도록 안전장치를 함께 둔 설계입니다.

### 프롬프트 인젝션 방어

현재 프롬프트는 사용자 질문을 `User Prompt`의 마지막 부분에 그대로 삽입합니다. 질문에 "이전 지시를 무시하고..." 같은 프롬프트 인젝션 시도가 들어오면 System Prompt가 1차 방어선으로 작동하지만, 완전한 방어는 아닙니다.

**운영 환경 이식 시 개선 방향**:
- 질문 입력 길이 제한 (현재 `min_length=1`만 있음)
- 알려진 인젝션 패턴 탐지 및 차단
- System Prompt의 제약 반복 강조

과제 범위에서는 내부 사내 문서 Q&A 환경을 가정해 이 부분은 최소한의 검증만 수행했습니다.

---

## 7. 요약

이 프로젝트의 프롬프트 설계는 **"LLM을 신뢰하지 않는다"** 는 관점에서 시작했습니다. LLM이 규칙을 지키도록 유도하되, 지키지 않을 경우에도 서비스가 안전하게 동작하도록 **프롬프트 / 구조화 응답 / 파싱 / 출처 매핑** 네 단계 방어선을 연결했습니다. 이 다중 방어 구조가 할루시네이션 억제의 핵심이며, 실제 통합 테스트에서 의도대로 동작하는 것을 확인했습니다.
