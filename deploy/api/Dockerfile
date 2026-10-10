# The API image for Railway (build context: the repository root). No secrets are baked in: everything comes from the environment.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
COPY --from=ghcr.io/astral-sh/uv:0.12.24 /uv /uvx /bin/
RUN useradd --create-home --uid 10001 app
WORKDIR /srv/app

# dependencies first (cached), then the code and the rule packs the API serves
COPY pyproject.toml uv.lock .python-version ./
RUN uv sync --frozen --no-dev --extra agents --extra observability --no-install-project
COPY apps/api ./apps/api
COPY rules ./rules
COPY skills ./skills
COPY docs/templates ./docs/templates
RUN uv sync --frozen --no-dev --extra agents --extra observability

USER app
ENV PATH="/srv/app/.venv/bin:$PATH" MEP_RULES_DIR=/srv/app/rules
EXPOSE 8000
# Railway sets $PORT. One worker per container: scale by adding replicas. (No --proxy-headers: the app reads the rightmost
# X-Forwarded-For hop itself where it needs a client key, and MEP_COOKIE_SECURE=1 decides cookie flags.)
CMD ["sh", "-c", "exec uvicorn --factory mep.api.server:app_from_env --host 0.0.0.0 --port ${PORT:-8000}"]
