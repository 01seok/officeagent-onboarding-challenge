import { useEffect, useRef, useState } from "react";

const SAMPLE_PROMPTS = [
  "연차는 어떻게 발생하나요?",
  "모든 PR은 어떤 조건에서 머지할 수 있나요?",
  "문서에 없는 내용이면 어떻게 응답하나요?",
];

const PRODUCT_PILLARS = [
  "문서 기반 답변",
  "출처 확인",
  "유사 질문 캐시",
];

const STATUS_LABELS = {
  processing: "처리 중",
  completed: "완료",
  failed: "실패",
};

async function requestData(url, options = {}) {
  const response = await fetch(url, options);
  const text = await response.text();
  const payload = text ? JSON.parse(text) : null;

  if (!response.ok || payload?.success === false) {
    throw new Error(payload?.message || "요청 처리 중 오류가 발생했습니다.");
  }

  return payload?.data ?? payload;
}

function formatCreatedAt(value) {
  if (!value) {
    return "-";
  }

  return new Date(value).toLocaleString("ko-KR", {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function sanitizeAnswerText(value) {
  if (!value) {
    return "";
  }

  return value
    .replace(/\s*\[Source\s+\d+\]/g, "")
    .replace(/[ \t]+\n/g, "\n")
    .replace(/\n{3,}/g, "\n\n")
    .trim();
}

function makeId() {
  return crypto.randomUUID();
}

export default function App() {
  const [documents, setDocuments] = useState([]);
  const [selectedDocId, setSelectedDocId] = useState("");
  const [question, setQuestion] = useState("");
  const [topK, setTopK] = useState(5);
  const [queryMode, setQueryMode] = useState("standard");
  const [uploadFile, setUploadFile] = useState(null);
  const [uploading, setUploading] = useState(false);
  const [loadingDocuments, setLoadingDocuments] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const [messages, setMessages] = useState([]);
  const [lastInsight, setLastInsight] = useState(null);
  const [notice, setNotice] = useState(
    "업로드된 문서에서 관련 내용을 찾고, 답변과 출처를 함께 제공합니다. 일반 응답과 스트리밍 응답을 모두 사용할 수 있습니다.",
  );
  const uploadInputRef = useRef(null);
  const composerInputRef = useRef(null);
  const streamCleanupRef = useRef(() => {});
  const messageThreadRef = useRef(null);
  const lastScrollTargetRef = useRef("");
  const isComposingRef = useRef(false);

  async function refreshDocuments({ silent = false } = {}) {
    if (!silent) {
      setLoadingDocuments(true);
    }

    try {
      const items = await requestData("/api/v1/documents");
      setDocuments(items);

      if (selectedDocId && !items.some((item) => item.doc_id === selectedDocId)) {
        setSelectedDocId("");
      }
    } catch (error) {
      setNotice(error.message);
    } finally {
      if (!silent) {
        setLoadingDocuments(false);
      }
    }
  }

  useEffect(() => {
    refreshDocuments();
  }, []);

  useEffect(() => {
    if (!documents.some((item) => item.status === "processing")) {
      return undefined;
    }

    const timerId = window.setTimeout(() => {
      refreshDocuments({ silent: true });
    }, 1400);

    return () => window.clearTimeout(timerId);
  }, [documents]);

  useEffect(() => () => streamCleanupRef.current(), []);

  useEffect(() => {
    if (!messages.length || !messageThreadRef.current) {
      return;
    }

    const lastMessage = messages[messages.length - 1];
    const nextScrollTarget = `${lastMessage.id}:${lastMessage.status}`;
    const isNewMessage = !lastScrollTargetRef.current.startsWith(lastMessage.id);
    const behavior = isNewMessage ? "smooth" : "auto";

    lastScrollTargetRef.current = nextScrollTarget;

    window.requestAnimationFrame(() => {
      const thread = messageThreadRef.current;
      if (!thread) {
        return;
      }

      thread.scrollTo({
        top: thread.scrollHeight,
        behavior,
      });
    });
  }, [messages]);

  async function handleUpload(event) {
    event.preventDefault();

    if (!uploadFile) {
      setNotice("업로드할 파일을 먼저 선택해주세요.");
      return;
    }

    const formData = new FormData();
    formData.append("file", uploadFile);

    setUploading(true);

    try {
      const data = await requestData("/api/v1/documents", {
        method: "POST",
        body: formData,
      });

      setNotice(
        `${data.filename} 문서를 연결했습니다. 백그라운드 처리 완료 후 문서 상태가 자동으로 갱신됩니다.`,
      );
      setUploadFile(null);

      if (uploadInputRef.current) {
        uploadInputRef.current.value = "";
      }

      await refreshDocuments();
    } catch (error) {
      setNotice(error.message);
    } finally {
      setUploading(false);
    }
  }

  async function handleDeleteDocument(docId, filename) {
    const confirmed = window.confirm(`${filename} 문서를 삭제할까요? 관련 캐시도 함께 무효화됩니다.`);
    if (!confirmed) {
      return;
    }

    try {
      await requestData(`/api/v1/documents/${docId}`, {
        method: "DELETE",
      });
      setNotice(`${filename} 문서를 삭제했습니다. 연관 캐시도 함께 무효화되었습니다.`);
      await refreshDocuments();
    } catch (error) {
      setNotice(error.message);
    }
  }

  async function submitQuestion() {
    const nextQuestion = (composerInputRef.current?.value ?? question).trim();
    if (!nextQuestion || submitting) {
      return;
    }

    const scopeLabel = selectedDocId
      ? documents.find((item) => item.doc_id === selectedDocId)?.filename ?? "선택 문서"
      : "전체 문서";

    const userMessage = {
      id: makeId(),
      role: "user",
      content: nextQuestion,
      scopeLabel,
    };

    const assistantId = makeId();
    const assistantPlaceholder = {
      id: assistantId,
      role: "assistant",
      content: "",
      mode: queryMode,
      status: "loading",
      scopeLabel,
    };

    setMessages((current) => [...current, userMessage, assistantPlaceholder]);
    setQuestion("");
    if (composerInputRef.current) {
      composerInputRef.current.value = "";
    }
    setSubmitting(true);
    streamCleanupRef.current();

    const payload = JSON.stringify({
      question: nextQuestion,
      top_k: Number(topK),
      doc_id: selectedDocId || null,
    });

    if (queryMode === "stream") {
      await submitStreamingQuery(payload, assistantId, nextQuestion, scopeLabel);
    } else {
      await submitStandardQuery(payload, assistantId, nextQuestion, scopeLabel);
    }

    setSubmitting(false);
  }

  function handleSamplePromptSelect(prompt) {
    setQuestion(prompt);
    window.requestAnimationFrame(() => {
      composerInputRef.current?.focus();
    });
  }

  async function handleSubmit(event) {
    event.preventDefault();
    await submitQuestion();
  }

  async function submitStandardQuery(payload, assistantId, originalQuestion, scopeLabel) {
    const startedAt = performance.now();

    try {
      const data = await requestData("/api/v1/query", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body: payload,
      });

      const cleanedAnswer = sanitizeAnswerText(data.answer);
      const insight = {
        mode: "standard",
        answer: cleanedAnswer,
        cacheHit: data.cache_hit,
        hasRelevantContent: data.has_relevant_content,
        sources: data.sources,
        durationMs: performance.now() - startedAt,
        scopeLabel,
        question: originalQuestion,
      };

      setMessages((current) =>
        current.map((message) =>
          message.id === assistantId
            ? {
                ...message,
                content: cleanedAnswer,
                status: "done",
                ...insight,
              }
            : message,
        ),
      );
      setLastInsight(insight);
      setNotice(
        data.cache_hit
          ? "동일하거나 유사한 질의가 캐시에 적중했습니다."
          : "관련 문서를 찾아 새로운 답변을 생성했습니다.",
      );
    } catch (error) {
      setMessages((current) =>
        current.map((message) =>
          message.id === assistantId
            ? {
                ...message,
                content: error.message,
                status: "error",
              }
            : message,
        ),
      );
      setNotice(error.message);
    }
  }

  async function submitStreamingQuery(payload, assistantId, originalQuestion, scopeLabel) {
    const startedAt = performance.now();

    try {
      const response = await fetch("/api/v1/query/stream", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body: payload,
      });

      if (!response.ok) {
        throw new Error("스트리밍 응답 생성에 실패했습니다.");
      }

      if (!response.body) {
        throw new Error("브라우저에서 스트리밍 응답을 지원하지 않습니다.");
      }

      const reader = response.body.getReader();
      const decoder = new TextDecoder("utf-8");
      let buffer = "";
      let rawAnswer = "";
      let pendingText = "";
      let flushTimerId = null;
      let waiter = null;

      const updateStreamingMessage = (content, status) => {
        setMessages((current) =>
          current.map((message) =>
            message.id === assistantId
              ? {
                  ...message,
                  content,
                  status,
                }
              : message,
          ),
        );
      };

      const stopFlusher = () => {
        if (flushTimerId !== null) {
          window.clearInterval(flushTimerId);
          flushTimerId = null;
        }

        if (!pendingText && waiter) {
          waiter();
          waiter = null;
        }
      };

      const startFlusher = () => {
        if (flushTimerId !== null) {
          return;
        }

        flushTimerId = window.setInterval(() => {
          if (!pendingText) {
            stopFlusher();
            return;
          }

          const sliceSize = Math.max(1, Math.min(12, Math.ceil(pendingText.length / 8)));
          rawAnswer += pendingText.slice(0, sliceSize);
          pendingText = pendingText.slice(sliceSize);
          updateStreamingMessage(sanitizeAnswerText(rawAnswer), "streaming");

          if (!pendingText) {
            stopFlusher();
          }
        }, 18);
      };

      const enqueueChunk = (chunk) => {
        if (!chunk || chunk === "[DONE]") {
          return;
        }

        pendingText += chunk;
        startFlusher();
      };

      const waitForFlush = async () => {
        if (!pendingText && flushTimerId === null) {
          return;
        }

        await new Promise((resolve) => {
          waiter = resolve;
        });
      };

      const consumeBlock = (block) => {
        const chunk = block
          .split("\n")
          .filter((line) => line.startsWith("data:"))
          .map((line) => line.replace(/^data:\s?/, ""))
          .join("\n");

        enqueueChunk(chunk);
      };

      const flushEventBuffer = () => {
        while (buffer.includes("\n\n")) {
          const boundary = buffer.indexOf("\n\n");
          const block = buffer.slice(0, boundary);
          buffer = buffer.slice(boundary + 2);
          consumeBlock(block);
        }
      };

      streamCleanupRef.current = () => {
        pendingText = "";
        stopFlusher();
      };

      while (true) {
        const { value, done } = await reader.read();

        if (done) {
          buffer += decoder.decode();
          flushEventBuffer();

          if (buffer.trim()) {
            consumeBlock(buffer.trim());
            buffer = "";
          }
          break;
        }

        buffer += decoder.decode(value, { stream: true });
        flushEventBuffer();
      }

      await waitForFlush();

      const cleanedAnswer = sanitizeAnswerText(rawAnswer) || "응답이 비어 있습니다.";
      const insight = {
        mode: "stream",
        answer: cleanedAnswer,
        durationMs: performance.now() - startedAt,
        scopeLabel,
        question: originalQuestion,
        metadataUnavailable: true,
      };

      updateStreamingMessage(cleanedAnswer, "done");
      setLastInsight(insight);
      setNotice("스트리밍 응답은 내용을 먼저 보여주고, 출처와 캐시 정보는 일반 응답 모드에서 확인할 수 있습니다.");
      streamCleanupRef.current = () => {};
    } catch (error) {
      setMessages((current) =>
        current.map((message) =>
          message.id === assistantId
            ? {
                ...message,
                content: error.message,
                status: "error",
              }
            : message,
        ),
      );
      setNotice(error.message);
      streamCleanupRef.current = () => {};
    }
  }

  const completedDocuments = documents.filter((item) => item.status === "completed");
  const selectedDocument = documents.find((item) => item.doc_id === selectedDocId);
  const scopeLabel = selectedDocument?.filename ?? "전체 문서";
  const modeLabel = queryMode === "standard" ? "일반 응답" : "스트리밍";

  return (
    <div className="demo-shell">
      <div className="ambient ambient-left" />
      <div className="ambient ambient-right" />

      <div className="topbar surface">
        <div className="brand-lockup">
          <span className="brand-mark">OA</span>
          <div>
            <p className="brand-name">OFFICEAGENT</p>
            <span className="brand-subtitle">사내 문서를 이해하고 근거와 함께 답변합니다</span>
          </div>
        </div>

        <div className="topbar-actions">
          <span className="topbar-chip">문서 연결</span>
          <span className="topbar-chip">질문 응답</span>
          <span className="topbar-chip">출처 확인</span>
        </div>
      </div>

      <div className="layout-grid">
        <div className="side-stack left-stack">
          <section className="surface mini-hero">
            <p className="eyebrow">Document-grounded answers for internal knowledge</p>
            <h1>사내 문서를 기반으로 정확하게 답변합니다</h1>
            <p className="hero-copy">
              업로드된 문서에서 관련 내용을 찾고, 답변과 함께 출처를 보여줍니다.
              문서에 근거가 없을 때는 관련 내용이 없다고 보수적으로 안내합니다.
            </p>

            <div className="hero-pill-row">
              {PRODUCT_PILLARS.map((item) => (
                <span key={item} className="hero-pill">
                  {item}
                </span>
              ))}
            </div>
          </section>

          <aside className="panel surface document-panel">
            <div className="panel-header">
              <div>
                <p className="panel-kicker">Document Hub</p>
                <h2>문서 관리</h2>
              </div>
              <button
                className="ghost-button"
                onClick={() => refreshDocuments()}
                type="button"
              >
                새로고침
              </button>
            </div>

            <form className="upload-form" onSubmit={handleUpload}>
              <div className="upload-dropzone">
                <span className="upload-title">PDF, TXT, Markdown 지원</span>
                <span className="upload-subtitle">
                  문서를 연결하면 백그라운드에서 청킹과 임베딩을 수행하고 검색 가능한
                  지식 베이스로 반영합니다.
                </span>

                <div className="upload-picker-row">
                  <label className="picker-button" htmlFor="officeagent-upload-input">
                    파일 선택
                  </label>
                  <span className="upload-filename">
                    {uploadFile ? uploadFile.name : "선택된 파일 없음"}
                  </span>
                </div>

                <input
                  ref={uploadInputRef}
                  id="officeagent-upload-input"
                  className="visually-hidden"
                  type="file"
                  accept=".pdf,.txt,.md,.markdown"
                  onChange={(event) => setUploadFile(event.target.files?.[0] ?? null)}
                />
              </div>

              <div className="upload-meta">
                <span>업로드 후 자동으로 문서 상태를 갱신합니다.</span>
                <button
                  className="solid-button"
                  disabled={uploading || !uploadFile}
                  type="submit"
                >
                  {uploading ? "업로드 중..." : "업로드 시작"}
                </button>
              </div>
            </form>

            <div className="doc-scope">
              <div className="scope-header">
                <h3>연결된 문서</h3>
                <span>{completedDocuments.length}개 문서 사용 가능</span>
              </div>

              <button
                type="button"
                className={`scope-chip ${selectedDocId === "" ? "is-active" : ""}`}
                onClick={() => setSelectedDocId("")}
              >
                전체 문서
              </button>

              <div className="document-list">
                {loadingDocuments ? (
                  <div className="empty-block">문서 목록을 불러오는 중입니다.</div>
                ) : documents.length === 0 ? (
                  <div className="empty-block">업로드된 문서가 아직 없습니다.</div>
                ) : (
                  documents.map((document) => (
                    <article
                      key={document.doc_id}
                      className={`document-card ${selectedDocId === document.doc_id ? "is-selected" : ""}`}
                    >
                      <button
                        type="button"
                        className="document-select"
                        onClick={() => {
                          if (document.status !== "completed") {
                            setNotice(
                              `${document.filename} 문서는 아직 ${STATUS_LABELS[document.status] ?? document.status} 상태라 질의 범위로 선택할 수 없습니다.`,
                            );
                            return;
                          }

                          setSelectedDocId((current) =>
                            current === document.doc_id ? "" : document.doc_id,
                          );
                        }}
                      >
                        <div>
                          <strong>{document.filename}</strong>
                          <p>{formatCreatedAt(document.created_at)}</p>
                        </div>
                        <span className={`status-badge status-${document.status}`}>
                          {STATUS_LABELS[document.status] ?? document.status}
                        </span>
                      </button>

                      <div className="document-footer">
                        <span>{document.chunk_count} chunks</span>
                        <button
                          type="button"
                          className="danger-link"
                          onClick={() =>
                            handleDeleteDocument(document.doc_id, document.filename)
                          }
                        >
                          삭제
                        </button>
                      </div>
                    </article>
                  ))
                )}
              </div>
            </div>
          </aside>
        </div>

        <main className="panel surface chat-panel">
          <div className="panel-header">
            <div>
              <p className="panel-kicker">OfficeAgent</p>
              <h2>질문하기</h2>
            </div>
            <div className="mode-switch">
              <button
                type="button"
                className={queryMode === "standard" ? "is-active" : ""}
                onClick={() => setQueryMode("standard")}
              >
                일반 응답
              </button>
              <button
                type="button"
                className={queryMode === "stream" ? "is-active" : ""}
                onClick={() => setQueryMode("stream")}
              >
                스트리밍
              </button>
            </div>
          </div>

          <div className="notice-banner">{notice}</div>

          <section ref={messageThreadRef} className="message-thread">
            {messages.length === 0 ? (
              <div className="thread-empty">
                <h3>무엇을 도와드릴까요?</h3>
                <p>
                  연결된 문서가 있다면 자유롭게 질문할 수 있고, 문서에 근거가 없으면
                  관련 내용이 없다고 안내합니다.
                </p>
              </div>
            ) : (
              messages.map((message) => (
                <article
                  key={message.id}
                  className={`message-card ${message.role === "user" ? "from-user" : "from-assistant"} ${message.status === "error" ? "is-error" : ""}`}
                >
                  <div className="message-head">
                    <span>{message.role === "user" ? "사용자" : "OfficeAgent"}</span>
                    <span>{message.scopeLabel}</span>
                  </div>

                  <div className="message-body">{message.content || "응답 생성 중..."}</div>

                  {message.role === "assistant" && message.status !== "loading" ? (
                    <div className="message-tags">
                      <span className="tag">{message.mode === "stream" ? "Stream" : "Standard"}</span>
                      {message.cacheHit ? <span className="tag success">Cache Hit</span> : null}
                      {message.hasRelevantContent === false ? (
                        <span className="tag muted">No Content</span>
                      ) : null}
                    </div>
                  ) : null}
                </article>
              ))
            )}
          </section>

          <div className="sample-prompt-row">
            {SAMPLE_PROMPTS.map((prompt) => (
              <button
                key={prompt}
                type="button"
                className="sample-chip"
                onClick={() => handleSamplePromptSelect(prompt)}
              >
                {prompt}
              </button>
            ))}
          </div>

          <form className="composer" onSubmit={handleSubmit}>
            <div className="composer-toolbar">
              <label>
                Top-K
                <select
                  value={topK}
                  onChange={(event) => setTopK(Number(event.target.value))}
                >
                  {[3, 5, 8, 10].map((value) => (
                    <option key={value} value={value}>
                      {value}
                    </option>
                  ))}
                </select>
              </label>

              <span className="scope-summary">
                현재 범위: {scopeLabel}
              </span>
            </div>

            <textarea
              ref={composerInputRef}
              value={question}
              onChange={(event) => setQuestion(event.target.value)}
              onCompositionStart={() => {
                isComposingRef.current = true;
              }}
              onCompositionEnd={(event) => {
                isComposingRef.current = false;
                setQuestion(event.currentTarget.value);
              }}
              placeholder="우리 회사 문서에 대해 질문해보세요. 예: 연차는 어떻게 발생하나요?"
              rows={3}
              onKeyDown={(event) => {
                if (event.nativeEvent.isComposing || isComposingRef.current) {
                  return;
                }

                if (event.key === "Enter" && !event.shiftKey) {
                  event.preventDefault();
                  submitQuestion();
                }
              }}
            />

            <div className="composer-actions">
              <p>
                {queryMode === "standard"
                  ? "일반 응답 모드에서는 캐시 적중 여부와 출처 문서를 함께 확인할 수 있습니다."
                  : "스트리밍 모드에서는 답변 내용을 먼저 확인하고, 일반 응답에서 메타 정보를 이어서 점검할 수 있습니다."}
              </p>
              <button className="solid-button accent-button" disabled={submitting} type="submit">
                {submitting ? "응답 생성 중..." : "질문 보내기"}
              </button>
            </div>
          </form>
        </main>

        <div className="side-stack right-stack">
          <section className="surface context-summary">
            <article>
              <span>연결된 문서</span>
              <strong>{documents.length}</strong>
            </article>
            <article>
              <span>질의 범위</span>
              <strong>{scopeLabel}</strong>
            </article>
            <article>
              <span>응답 모드</span>
              <strong>{modeLabel}</strong>
            </article>
          </section>

          <aside className="panel surface insight-panel">
            <div className="panel-header">
              <div>
                <p className="panel-kicker">Answer Insight</p>
                <h2>응답 정보</h2>
              </div>
            </div>

            {!lastInsight ? (
              <div className="empty-block">
                질의를 실행하면 캐시 여부, grounded 여부, 출처 청크 정보를 이곳에서
                확인할 수 있습니다.
              </div>
            ) : (
              <div className="insight-stack">
                <section className="insight-card">
                  <div className="insight-grid">
                    <article>
                      <span>질의 모드</span>
                      <strong>{lastInsight.mode === "standard" ? "일반 응답" : "스트리밍"}</strong>
                    </article>
                    <article>
                      <span>질의 범위</span>
                      <strong>{lastInsight.scopeLabel}</strong>
                    </article>
                    <article>
                      <span>응답 시간</span>
                      <strong>{Math.round(lastInsight.durationMs)} ms</strong>
                    </article>
                    <article>
                      <span>질문</span>
                      <strong>{lastInsight.question}</strong>
                    </article>
                  </div>

                  {lastInsight.mode === "standard" ? (
                    <div className="badge-row">
                      <span className={`pill ${lastInsight.cacheHit ? "pill-success" : ""}`}>
                        {lastInsight.cacheHit ? "Cache Hit" : "Fresh Response"}
                      </span>
                      <span
                        className={`pill ${lastInsight.hasRelevantContent === false ? "pill-muted" : "pill-warning"}`}
                      >
                        {lastInsight.hasRelevantContent === false
                          ? "No Relevant Content"
                          : "Grounded Answer"}
                      </span>
                      <span className="pill">Sources {lastInsight.sources?.length ?? 0}</span>
                    </div>
                  ) : (
                    <div className="stream-note">
                      현재 스트리밍 응답은 답변 내용을 먼저 보여주고, 출처와 캐시 정보는
                      일반 응답 모드에서 함께 확인할 수 있습니다.
                    </div>
                  )}
                </section>

                {lastInsight.mode === "standard" ? (
                  <section className="insight-card">
                    <h3>출처 청크</h3>
                    {lastInsight.sources?.length ? (
                      <div className="source-list">
                        {lastInsight.sources.map((source) => (
                          <article key={`${source.doc_id}-${source.chunk_index}`} className="source-card">
                            <div className="source-head">
                              <strong>{source.filename}</strong>
                              <span>chunk #{source.chunk_index}</span>
                            </div>
                            <p>{source.text}</p>
                            <small>RRF score {source.score.toFixed(4)}</small>
                          </article>
                        ))}
                      </div>
                    ) : (
                      <div className="empty-block">
                        관련 출처가 없을 때는 sources를 비워 환각 가능성을 낮춥니다.
                      </div>
                    )}
                  </section>
                ) : null}
              </div>
            )}
          </aside>
        </div>
      </div>
    </div>
  );
}
