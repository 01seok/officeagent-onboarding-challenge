import asyncio
import json
import logging
import re
import tempfile
from pathlib import Path
from typing import AsyncGenerator

from pydantic import BaseModel, ValidationError

logger = logging.getLogger(__name__)


# LLM이 반환해야 할 구조화 응답
class LLMAnswerResult(BaseModel):
    answer: str
    source_indices: list[int]
    has_relevant_content: bool


# Codex 최종 응답을 강제할 JSON Schema
_ANSWER_SCHEMA = {
    "type": "object",
    "properties": {
        "answer": {"type": "string"},
        "source_indices": {
            "type": "array",
            "items": {"type": "integer", "minimum": 0},
        },
        "has_relevant_content": {"type": "boolean"},
    },
    "required": ["answer", "source_indices", "has_relevant_content"],
    "additionalProperties": False,
}

_STRUCTURED_INSTRUCTIONS = """
당신은 제공된 문서를 기반으로만 답변하는 Q&A 어시스턴트입니다.
규칙:
  1. 반드시 [Source N] 태그로 표시된 문서 내용만 근거로 사용하세요.
  2. 문서에 관련 내용이 없으면 has_relevant_content를 false로 설정하고, answer에 '제공된 문서에서 관련 내용을 찾을 수 없습니다.'라고 작성하세요.
  3. source_indices에는 실제로 근거로 사용한 Source 번호만 담으세요.
  4. 최종 응답은 JSON Schema를 반드시 만족해야 합니다.
""".strip()

_STREAM_INSTRUCTIONS = """
당신은 제공된 문서를 기반으로만 답변하는 Q&A 어시스턴트입니다.
규칙:
  1. 반드시 [Source N] 태그로 표시된 문서 내용만 근거로 사용하세요.
  2. 문서에 관련 내용이 없으면 '제공된 문서에서 관련 내용을 찾을 수 없습니다.'라고 답하세요.
  3. 자연스러운 문장으로 간결하게 답변하세요.
""".strip()


class LLMService:
    # Codex CLI를 감싸는 LLM 어댑터
    def __init__(
        self,
        codex_bin: str = "codex",
        model: str | None = None,
        sandbox: str = "read-only",
    ) -> None:
        self._codex_bin = codex_bin
        self._model = model
        self._sandbox = sandbox

    # 일반 질의응답 : 구조화 응답을 받아 Pydantic으로 파싱
    async def generate_answer(
        self, question: str, chunks: list[dict]
    ) -> LLMAnswerResult:
        prompt = self._build_prompt(question, chunks, _STRUCTURED_INSTRUCTIONS)
        raw = await self._run_exec(prompt, output_schema=_ANSWER_SCHEMA)
        return self._parse_response(raw)

    # 스트리밍 질의응답 : Codex JSON 이벤트에서 텍스트만 추출
    async def generate_answer_stream(
        self, question: str, chunks: list[dict]
    ) -> AsyncGenerator[str, None]:
        prompt = self._build_prompt(question, chunks, _STREAM_INSTRUCTIONS)
        async for token in self._run_exec_stream(prompt):
            yield token

    # 검색된 청크를 Source 블록으로 붙여 Codex에 전달할 최종 프롬프트 생성
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

    # codex exec 일반 모드 호출 : stdout의 마지막 메시지를 최종 응답으로 사용
    async def _run_exec(
        self,
        prompt: str,
        output_schema: dict | None = None,
    ) -> str:
        command, schema_path = self._build_exec_command(
            prompt,
            output_schema=output_schema,
        )
        process = await asyncio.create_subprocess_exec(
            *command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await process.communicate()
        finally:
            if schema_path is not None:
                Path(schema_path).unlink(missing_ok=True)

        stderr_text = stderr.decode("utf-8", errors="replace").strip()
        if stderr_text:
            # 진행 로그와 fallback 경고는 stderr로만 남기고 응답 파싱에는 쓰지 않음
            logger.info("codex exec stderr: %s", self._shrink(stderr_text))

        raw_output = stdout.decode("utf-8", errors="replace").strip()

        if process.returncode != 0:
            logger.warning(
                "codex exec failed with code %s: %s",
                process.returncode,
                self._shrink(stderr_text),
            )
            if not raw_output:
                raise RuntimeError("codex exec failed without a final response")

        if not raw_output:
            raise RuntimeError("codex exec returned an empty response")

        return raw_output

    # codex exec --json 호출 : agent_message 이벤트의 delta만 SSE로 전달
    async def _run_exec_stream(self, prompt: str) -> AsyncGenerator[str, None]:
        command, _ = self._build_exec_command(prompt, json_output=True)
        process = await asyncio.create_subprocess_exec(
            *command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        stderr_task = asyncio.create_task(self._read_stream(process.stderr))
        emitted: dict[str, str] = {}
        yielded_any = False

        try:
            assert process.stdout is not None
            while True:
                raw_line = await process.stdout.readline()
                if not raw_line:
                    break

                line = raw_line.decode("utf-8", errors="replace").strip()
                if not line:
                    continue

                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue

                # 같은 메시지가 누적 갱신되므로 새로 늘어난 부분만 전달
                for token in self._extract_stream_tokens(event, emitted):
                    yielded_any = True
                    yield token

            await process.wait()
        finally:
            stderr_text = await stderr_task

        if stderr_text:
            logger.info("codex exec stream stderr: %s", self._shrink(stderr_text))

        if process.returncode != 0:
            logger.warning(
                "codex exec stream failed with code %s: %s",
                process.returncode,
                self._shrink(stderr_text),
            )
            if not yielded_any:
                yield "답변을 생성할 수 없습니다."

    # 실행 모드에 맞는 codex exec 명령어 조립
    def _build_exec_command(
        self,
        prompt: str,
        *,
        output_schema: dict | None = None,
        json_output: bool = False,
    ) -> tuple[list[str], str | None]:
        command = [
            self._codex_bin,
            "exec",
            "--ephemeral",
            "--sandbox",
            self._sandbox,
            "--skip-git-repo-check",
        ]

        if self._model:
            command.extend(["--model", self._model])

        if json_output:
            command.append("--json")

        schema_path: str | None = None
        if output_schema is not None:
            with tempfile.NamedTemporaryFile(
                mode="w",
                suffix=".json",
                encoding="utf-8",
                delete=False,
            ) as handle:
                json.dump(output_schema, handle, ensure_ascii=False)
                schema_path = handle.name

            command.extend(["--output-schema", schema_path])

        command.append(prompt)
        return command, schema_path

    # stderr를 끝까지 읽어 subprocess 교착 상태를 방지
    async def _read_stream(self, stream: asyncio.StreamReader | None) -> str:
        if stream is None:
            return ""

        chunks: list[str] = []
        while True:
            raw_line = await stream.readline()
            if not raw_line:
                break
            chunks.append(raw_line.decode("utf-8", errors="replace"))
        return "".join(chunks).strip()

    # JSONL 이벤트에서 agent_message 텍스트 delta만 추출
    def _extract_stream_tokens(
        self,
        event: dict,
        emitted: dict[str, str],
    ) -> list[str]:
        item = event.get("item")
        if not isinstance(item, dict) or item.get("type") != "agent_message":
            return []

        item_id = str(item.get("id", "final"))
        text = item.get("text")
        if not isinstance(text, str) or not text:
            return []

        previous = emitted.get(item_id, "")
        emitted[item_id] = text

        if text.startswith(previous):
            delta = text[len(previous):]
        else:
            delta = text

        return [delta] if delta else []

    # stdout에서 JSON 객체를 찾아 구조화 응답으로 파싱
    def _parse_response(self, raw: str) -> LLMAnswerResult:
        match = re.search(r"\{.*\}", raw, re.DOTALL)
        if match:
            try:
                return LLMAnswerResult.model_validate_json(match.group())
            except (ValidationError, ValueError):
                pass

        return LLMAnswerResult(
            answer=raw.strip() or "답변을 생성할 수 없습니다.",
            source_indices=[],
            has_relevant_content=False,
        )

    # 로그 폭주를 막기 위해 stderr를 잘라서 남김
    def _shrink(self, text: str, limit: int = 1500) -> str:
        trimmed = text.strip()
        if len(trimmed) <= limit:
            return trimmed
        return trimmed[:limit] + "...(truncated)"
