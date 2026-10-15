# FlyFit API image (Render runs it; CI builds and smoke-tests it on every PR).
# Two stages: dependencies are installed with uv in the first, and only the virtualenv and
# the app code are copied into the final image.
FROM python:3.12-slim-bookworm AS build
COPY --from=ghcr.io/astral-sh/uv:0.12.23 /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
WORKDIR /srv/flyfit/api
# Dependencies first, in their own layer: a code-only change reuses it.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

FROM python:3.12-slim-bookworm
RUN useradd --create-home --uid 10001 flyfit
# The code sits at /srv/flyfit/api so the repo-relative paths in app/config.py still work
# (e.g. the email outbox folder used when RESEND_API_KEY is empty).
WORKDIR /srv/flyfit/api
COPY --from=build /srv/flyfit/api/.venv .venv
COPY alembic.ini ./
COPY migrations migrations
COPY app app
RUN chown -R flyfit /srv/flyfit
USER flyfit
ENV PATH="/srv/flyfit/api/.venv/bin:$PATH" PYTHONUNBUFFERED=1 APP_ENV=production PORT=8000
EXPOSE 8000
# Migrations run on start: the free tier has no pre-deploy step, and there is only ever one
# instance, so two migrations can't race. A failed migration stops the deploy before any
# traffic reaches the new code (Render keeps the old instance until /health passes).
# --timeout-keep-alive 75 must outlast the Next.js proxy's idle connections (api/README.md).
CMD ["sh", "-c", "alembic upgrade head && exec uvicorn app.main:app --host 0.0.0.0 --port \"$PORT\" --timeout-keep-alive 75 --no-server-header"]
