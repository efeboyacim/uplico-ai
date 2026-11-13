FROM python:3.12-slim
COPY --from=ghcr.io/astral-sh/uv:0.9.18 /uv /uvx /bin/
WORKDIR /app
ENV PYTHONUNBUFFERED=1 UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy PATH="/app/.venv/bin:$PATH"
COPY pyproject.toml uv.lock ./
ARG LLM_EXTRAS="openai anthropic"
RUN set -f; set --; for e in $LLM_EXTRAS; do set -- "$@" --extra "$e"; done; uv sync --locked --no-dev "$@"
COPY src ./src
EXPOSE 8000
CMD ["python", "-m", "src.api"]
