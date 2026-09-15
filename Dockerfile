FROM python:3.12-slim AS builder

COPY --from=ghcr.io/astral-sh/uv:0.9.28 /uv /usr/local/bin/uv

ENV PYTHONDONTWRITEBYTECODE=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

WORKDIR /build
ENV UV_PROJECT_ENVIRONMENT=/opt/venv
COPY pyproject.toml uv.lock README.md LICENSE ./
COPY tradingagents ./tradingagents
COPY cli ./cli
RUN uv sync --locked --no-dev --no-editable

FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

RUN useradd --create-home appuser \
 && install -d -m 0755 -o appuser -g appuser /home/appuser/.tradingagents /home/appuser/app
USER appuser
WORKDIR /home/appuser/app

# Only the installed environment crosses into the runtime image. Do not copy
# the build checkout, local reports, credentials or host caches.

ENTRYPOINT ["tradingagents"]
