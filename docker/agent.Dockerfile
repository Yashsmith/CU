# V1 agent runtime (control plane). The desktop body is desktop-sandbox (see docker-compose.yml).
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app

COPY pyproject.toml README.md ./
COPY agent/ ./agent/
COPY sandbox/ ./sandbox/
RUN pip install --no-cache-dir -e .

# Sessions (meta.json + events.jsonl + PNGs) are written here.
VOLUME ["/app/sessions"]

# API: Groq vision (qwen/qwen3.8-27b). Body: desktop-sandbox over HTTP.
ENV MODEL=qwen/qwen3.8-27b SANDBOX_BACKEND=http SANDBOX_URL=http://sandbox:7090

ENTRYPOINT ["python", "-m", "agent.main"]
CMD ["--help"]
