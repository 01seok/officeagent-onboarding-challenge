import logging
import re

from claude_code_sdk import query, ClaudeCodeOptions
from claude_code_sdk._errors import MessageParseError
from pydantic import BaseModel, ValidationError

logger = logging.getLogger(__name__)

# LLM이 반환해야 할 Json 구조, 파싱 실패 즉시 감지
class LLMAnswerResult(BaseModel):
    answer: str
    source_indices: list[int]
    has_relevant_content: bool

# role 부여 + 규칙 + json 포맷을 담은 프롬프트
_SYSTEM_PROMPT = """
당신은 제공된 문서를 기반으로만 답변하는 Q&A 어시스턴트입니다.
규칙:
  1. 반드시 [Source N] 태그로 표시된 문서 내용만 근거로 사용하세요.
  2. 문서에 관련 내용이 없으면 has_relevant_content를 false로 설정하고, answer에 '제공된 문서에서 관련 내용을 찾을 수 없습니다.'라고 작성하세요.
  3. 반드시 아래 JSON 형식으로만 응답하세요. 다른 텍스트는 포함하지 마세요:
  {"answer": "답변 내용", "source_indices": [0, 1], "has_relevant_content": true}
"""

# 스트리밍 전용 프롬프트 : JSON 강제 없이 자연스러운 문장으로 답변
_STREAM_SYSTEM_PROMPT = """
당신은 제공된 문서를 기반으로만 답변하는 Q&A 어시스턴트입니다.

규칙:
1. 반드시 [Source N] 태그로 표시된 문서 내용만 근거로 사용하세요.
2. 문서에 관련 내용이 없으면 '제공된 문서에서 관련 내용을 찾을 수 없습니다.'라고 답하세요.
3. 자연스러운 문장으로 간결하게 답변하세요.
"""

class LLMService:
    
    async def generate_answer(
        self, question: str, chunks: list[dict]
    ) -> LLMAnswerResult:
        prompt = self._build_user_prompt(question, chunks)
        raw_parts: list[str] = []

        try:
            # query()는 async generator, 메시지를 스트리밍으로 수신하므로 async for로 소비
            async for message in query(
                prompt=prompt,
                options=ClaudeCodeOptions(
                    system_prompt=_SYSTEM_PROMPT,
                    max_turns=1,  # 단발성 응답만 필요, 다음 턴 없음
                ),
            ):
                # AssistantMessage는 content가 블록 리스트로 구성
                if hasattr(message, "content") and isinstance(message.content, list):
                    for block in message.content:
                        if hasattr(block, "text"):
                            raw_parts.append(block.text)
        except MessageParseError as e:
            # SDK가 rate_limit_event 등 informational 메시지 타입을 case에 안 넣어서 예외 발생
            # assistant 메시지가 먼저 왔다면 raw_parts에 답변이 이미 쌓였을 수 있어 그대로 파싱
            logger.warning("claude-code-sdk MessageParseError, 누적된 응답으로 파싱 시도: %s", e)

        return self._parse_response("".join(raw_parts))

    def _build_user_prompt(self, question: str, chunks: list[dict]) -> str:
        # 청크마다 번호를 붙여 LLM이 source_indices로 참조할 수 있게 구성
        sources = "\n\n".join(
            f"[Source {i}] 문서: {c['filename']}\n---\n{c['text']}\n---"
            for i, c in enumerate(chunks)
        )
        return f"{sources}\n\n질문: {question}"

    def _parse_response(self, raw: str) -> LLMAnswerResult:
        # LLM이 JSON 앞뒤에 설명이나 코드펜스를 붙이는 경우를 대비해 중괄호 블록만 추출
        match = re.search(r"\{.*\}", raw, re.DOTALL)
        if match:
            try:
                return LLMAnswerResult.model_validate_json(match.group())
            except (ValidationError, ValueError):
                pass
        # 파싱 완전 실패 시 원본 텍스트를 answer로 담아 반환
        return LLMAnswerResult(
            answer=raw.strip() or "답변을 생성할 수 없습니다.",
            source_indices=[],
            # JSON 파싱 실패한 응답은 근거 있는 답변으로 분류하지 않음
            has_relevant_content=False,
        )


    async def generate_answer_stream(
        self, question: str, chunks: list[dict]
    ):
        # async generator, 토큰이 올 때마다 즉시 yield (모아서 반환하지 않음)
        prompt = self._build_user_prompt(question, chunks)

        try:
            async for message in query(
                prompt=prompt,
                options=ClaudeCodeOptions(
                    system_prompt=_STREAM_SYSTEM_PROMPT,
                    max_turns=1,
                ),
            ):
                if hasattr(message, "content") and isinstance(message.content, list):
                    for block in message.content:
                        # 텍스트 블록이 있을 때만 yield, 빈 문자열은 제외
                        if hasattr(block, "text") and block.text:
                            yield block.text
        except MessageParseError as e:
            # SDK가 rate_limit_event 등 informational 타입을 모르는 경우, 스트림 종료만 처리
            logger.warning("claude-code-sdk MessageParseError, 스트리밍 중단: %s", e)