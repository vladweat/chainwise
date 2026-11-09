FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    EMBEDDING_MODEL_PATH=/opt/models \
    HF_HUB_OFFLINE=1 \
    TRANSFORMERS_OFFLINE=1 \
    LD_LIBRARY_PATH=/usr/local/lib

WORKDIR /app

# Offline-сборка: пакеты берутся из локального venv (make venv).
# На хосте должен быть Python 3.12 — та же версия, что в базовом образе.
COPY .venv/lib/python3.12/site-packages/ /usr/local/lib/python3.12/site-packages/

COPY alembic.ini .
COPY alembic ./alembic
COPY app ./app
COPY tests ./tests
COPY ml_models ./ml_models
COPY .models /opt/models
COPY .libs/ /usr/local/lib/

EXPOSE 8000

CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
