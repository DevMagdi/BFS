FROM python:3.11-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /app

RUN addgroup --system app && adduser --system --ingroup app app
COPY pyproject.toml README.md alembic.ini ./
COPY src ./src
COPY migrations ./migrations
RUN python -m pip install --no-cache-dir .
USER app
EXPOSE 8000

FROM base AS development
USER root
COPY tests ./tests
RUN python -m pip install --no-cache-dir '.[dev]' \
    && mkdir -p /tmp/pytest-cache \
    && chown app:app /tmp/pytest-cache
ENV PYTEST_ADDOPTS=--cache-dir=/tmp/pytest-cache
USER app

FROM base AS runtime
CMD ["uvicorn", "businessflow.main:app", "--host", "0.0.0.0", "--port", "8000"]
