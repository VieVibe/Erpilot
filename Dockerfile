FROM node:22-bookworm-slim AS frontend
WORKDIR /build
COPY apps/web/package.json apps/web/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY apps/web/ ./
RUN npm run build

FROM python:3.12.10-slim-bookworm
ENV PYTHONUNBUFFERED=1 PYTHONIOENCODING=utf-8 \
    UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never \
    ERPILOT_DATA_DIR=/data ERPILOT_RUN_STORE_PATH=/data/erpilot-runs.db \
    ERPILOT_WEB_DIST=/app/apps/web/dist ERPILOT_HOST=0.0.0.0
WORKDIR /app
RUN pip install --no-cache-dir uv==0.13.7
COPY pyproject.toml uv.lock README.md .python-version ./
COPY packages/ ./packages/
COPY apps/api/ ./apps/api/
COPY apps/cli/ ./apps/cli/
RUN --mount=type=cache,id=erpilot-uv,target=/root/.cache/uv uv sync --all-packages --frozen --no-dev
COPY --from=frontend /build/dist ./apps/web/dist
RUN groupadd --gid 10001 erpilot && useradd --uid 10001 --gid 10001 --no-create-home erpilot \
    && mkdir -p /data && chown 10001:10001 /data
ENV PATH="/app/.venv/bin:$PATH"
USER 10001:10001
EXPOSE 8000
CMD ["python", "-m", "erpilot_api.deployment"]
