FROM python:3.12-slim

WORKDIR /app

# System deps for reportlab, python-pptx
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc libpq-dev && \
    rm -rf /var/lib/apt/lists/*

COPY pyproject.toml .
RUN pip install --no-cache-dir .

COPY . .

ENV PORT=8080
EXPOSE 8080

CMD ["uvicorn", "orchestrator.main:app", "--host", "0.0.0.0", "--port", "8080"]
