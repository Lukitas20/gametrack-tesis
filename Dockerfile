FROM python:3.13-slim AS builder
WORKDIR /build
COPY backend/requirements*.txt ./
RUN --mount=type=secret,id=build_ca --mount=type=cache,target=/root/.cache/pip \
    if [ -f /run/secrets/build_ca ]; then export PIP_CERT=/run/secrets/build_ca; fi; \
    pip wheel --retries 10 --timeout 120 --progress-bar off --wheel-dir /wheels -r requirements-postgres.txt

FROM python:3.13-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    REVIEW_MODEL_PATH=/app/backend/models/review_model.npz \
    STEAM_AFFINITY_MODEL_PATH=/app/backend/models/steam_affinity.json \
    OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1
RUN useradd --create-home --uid 10001 gametrack
COPY --from=builder /wheels /wheels
RUN pip install --no-cache-dir --no-index /wheels/*.whl && rm -rf /wheels
WORKDIR /app/backend
COPY --chown=gametrack:gametrack backend /app/backend
COPY --chown=gametrack:gametrack frontend /app/frontend
RUN python scripts/verify_review_model.py
RUN python scripts/verify_steam_affinity_model.py
RUN mkdir -p data/generated && chown gametrack:gametrack data/generated
USER gametrack
EXPOSE 8000
CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
