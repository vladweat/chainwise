FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    SOLC_DIR=/opt/solc \
    HOME=/tmp \
    TMPDIR=/tmp

WORKDIR /app

# Offline-сборка: пакеты берутся из локального venv (make venv-slither).
COPY .venv/lib/python3.12/site-packages/ /usr/local/lib/python3.12/site-packages/

COPY app.py .

USER 10001:10001

EXPOSE 8000

CMD ["python", "-m", "uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]
