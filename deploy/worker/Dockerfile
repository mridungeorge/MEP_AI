# The drafting skill worker image (build context: the repository root). One job per container: the dispatcher starts it with
#   --network none --read-only --user 10001 --cap-drop ALL, the job input mounted read-only and /job/out the only writable place.
# It carries what the skills need (ifcopenshell, ezdxf, and the CAD kernel cadquery for duct-fab); the API image does not.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
# runtime libraries the OpenCascade wheels load
RUN apt-get update && apt-get install -y --no-install-recommends libgl1 libglu1-mesa libxrender1 libxext6 libsm6 libx11-6 \
    && rm -rf /var/lib/apt/lists/*
COPY --from=ghcr.io/astral-sh/uv:0.12.24 /uv /uvx /bin/
WORKDIR /srv/app

COPY pyproject.toml uv.lock .python-version ./
RUN uv sync --frozen --no-dev --no-install-project
COPY apps/api ./apps/api
COPY skills ./skills
RUN uv sync --frozen --no-dev \
    && uv pip install --python .venv/bin/python cadquery==2.8.0 cadquery-ocp==7.9.3.1.1 \
    && .venv/bin/python -c "import cadquery, ifcopenshell, ezdxf, jsonschema"

RUN useradd --uid 10001 --no-create-home --shell /usr/sbin/nologin worker
USER 10001:10001
ENV PATH="/srv/app/.venv/bin:$PATH" HOME=/tmp
WORKDIR /job
ENTRYPOINT ["python", "-m", "mep.skills_runner.worker"]
