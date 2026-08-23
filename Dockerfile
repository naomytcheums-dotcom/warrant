FROM python:3.12-slim

WORKDIR /app

# python:slim has no git -- `pip install git+https://...` needs it to clone
# warren/ledger, which aren't on PyPI yet. Missing this is a one-line error
# ("git: not found") that's easy to not notice until the image actually builds.
RUN apt-get update && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir \
    "git+https://github.com/naomytcheums-dotcom/warren.git" \
    "git+https://github.com/naomytcheums-dotcom/ledger.git"

COPY . .
RUN pip install --no-cache-dir .

EXPOSE 8000
CMD ["sh", "-c", "uvicorn warrant.service:app --host 0.0.0.0 --port ${PORT:-8000}"]
