# syntax=docker/dockerfile:1
# Multi-stage build with uv. Build:  docker build -t clinic-manager .
# Choose provider extras at build time, e.g. --build-arg EXTRAS="google firestore" (default) or "caldav firestore".

ARG PYTHON_IMAGE=python:3.12-slim

FROM ${PYTHON_IMAGE} AS build
COPY --from=ghcr.io/astral-sh/uv:0.11.32 /uv /bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=0
ARG EXTRAS="google firestore"
WORKDIR /app

COPY pyproject.toml uv.lock README.md ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-install-project $(for e in $EXTRAS; do printf -- "--extra %s " "$e"; done)

COPY app ./app
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-editable $(for e in $EXTRAS; do printf -- "--extra %s " "$e"; done)

FROM ${PYTHON_IMAGE}
RUN useradd --system --uid 10001 --home /app app
WORKDIR /app
COPY --from=build /app/.venv /app/.venv
COPY config ./config
COPY skills ./skills
ENV PATH="/app/.venv/bin:$PATH" PYTHONUNBUFFERED=1 PORT=8080
USER app
EXPOSE 8080
CMD ["sh", "-c", "exec uvicorn app.main:create_app --factory --host 0.0.0.0 --port ${PORT} --proxy-headers --forwarded-allow-ips='*'"]
