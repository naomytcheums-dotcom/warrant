FROM python:3.12-slim

WORKDIR /app

RUN pip install --no-cache-dir \
    "git+https://github.com/naomytcheums-dotcom/warren.git" \
    "git+https://github.com/naomytcheums-dotcom/ledger.git"

COPY . .
RUN pip install --no-cache-dir .

EXPOSE 8000
CMD ["sh", "-c", "uvicorn warrant.service:app --host 0.0.0.0 --port ${PORT:-8000}"]
