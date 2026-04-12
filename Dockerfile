FROM python:3.12-slim

WORKDIR /app

# 시스템 의존성 + Node.js (claude CLI 실행에 필요)
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    curl \
    && curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y nodejs \
    && rm -rf /var/lib/apt/lists/*

# claude CLI 설치 (claude-code-sdk가 내부적으로 호출)
RUN npm install -g @anthropic-ai/claude-code

# 의존성 먼저 설치 (레이어 캐시 활용)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 임베딩 모델 사전 다운로드 (컨테이너 시작 시 지연 방지)
RUN python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('intfloat/multilingual-e5-small')"

# 소스 복사
COPY . .

# 데이터 디렉토리 생성
RUN mkdir -p /app/data/chroma /app/uploads

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
