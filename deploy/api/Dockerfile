# The API image for Railway (build context: the repository root). No secrets are baked in: everything comes from the environment.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
RUN pip install --no-cache-dir uv==0.5.* \
 && useradd --create-home --uid 10001 app
WORKDIR /srv/app

# dependencies first (cached), then the code and the rule packs the API serves
COPY pyproject.toml uv.lock .python-version ./
RUN uv sync --frozen --no-dev --no-install-project
COPY apps/api ./apps/api
COPY rules ./rules
COPY docs/templates ./docs/templates
RUN uv sync --frozen --no-dev

USER app
ENV PATH="/srv/app/.venv/bin:$PATH" MEP_RULES_DIR=/srv/app/rules
EXPOSE 8000
# Railway sets $PORT. One worker per container: scale by adding replicas.
CMD ["sh", "-c", "exec uvicorn --factory mep.api.server:app_from_env --host 0.0.0.0 --port ${PORT:-8000} --proxy-headers --forwarded-allow-ips='*'"]
