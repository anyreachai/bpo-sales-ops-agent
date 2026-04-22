FROM python:3.12-slim

WORKDIR /app

# System deps for reportlab, python-pptx, pycairo (xhtml2pdf → svglib)
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc libpq-dev pkg-config libcairo2-dev meson && \
    rm -rf /var/lib/apt/lists/*

COPY . .
RUN pip install --no-cache-dir .

ENV PORT=8080
EXPOSE 8080

CMD ["uvicorn", "orchestrator.main:app", "--host", "0.0.0.0", "--port", "8080"]
