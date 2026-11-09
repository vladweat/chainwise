# Установка и запуск

## Требования

| Компонент | Версия |
|---|---|
| Docker + Docker Compose | Compose v2.24+ |
| Python (на хосте) | **3.12** |
| Node.js + npm (на хосте) | **20+** |
| curl | для smoke test |

Базовые образы (`python:3.12-slim`, `node:20-alpine`, `postgres`/`pgvector`, `redis:7`, `qdrant`) нужно один раз скачать, пока есть интернет.

## Шаг 1 — установка зависимостей (с интернетом)

```bash
cp .env.example .env
make deps
```

Команда `make deps` выполняет `venv` + `venv-slither` + `deps-frontend`.

| Target | Действие |
|---|---|
| `make venv` | `backend/.venv` + pip + solc 0.8.24 + MiniLM ONNX в `backend/.models` |
| `make solc` | только бинарник solc 0.8.24 (venv уже есть) |
| `make embeddings` | только модель `all-MiniLM-L6-v2` |
| `make venv-slither` | `slither/.venv` + pip (`slither-analyzer`) |
| `make deps-frontend` | `frontend/node_modules` |

Отдельно:

```bash
make venv
make embeddings
make venv-slither
make deps-frontend
```

Если `backend/.venv` уже есть и нужно только MiniLM + Qdrant-клиент (ONNX, без torch):

```bash
cd backend
source .venv/bin/activate
pip uninstall -y torch sympy networkx mpmath 2>/dev/null || true
pip install -r requirements.txt
python scripts/prepare_embeddings.py
cd ..
make up
```

## Шаг 2 — запуск (offline)

```bash
make up
```

Контейнеры: postgres, redis, qdrant, slither, backend, celery-worker, frontend.

Миграции Alembic применяются при старте backend. Вручную:

```bash
make migrate
```

## Проверка

```bash
make health
# или
curl http://localhost:8001/health
```

Ожидаемый ответ:

```json
{
  "status": "ok",
  "environment": "development",
  "postgres": "ok",
  "redis": "ok",
  "qdrant": "ok",
  "slither": "ok"
}
```

Открыть в браузере:

- http://localhost:3001 — frontend
- http://localhost:8001/docs — Swagger
- http://localhost:6333/dashboard — Qdrant

Sentry на localhost не нужен: `SENTRY_DSN` в `.env` оставьте пустым. Подключение облака — [environment.md § Sentry](./environment.md#sentry).

## Полезные команды

```bash
make logs         # логи backend + frontend
make logs-frontend
make test         # pytest в контейнере backend
make lint         # flake8 в контейнере backend
make train-risk   # переобучить XGBoost на синтетике
make up-fast      # up без пересборки образов
make up-frontend  # пересобрать только frontend
make down
make build        # пересобрать образы (--no-cache)
```

## CI

На каждый `push` и `pull_request`: [`.github/workflows/ci.yml`](../.github/workflows/ci.yml).

| Job | Что делает |
|---|---|
| `backend-lint` | flake8 (`backend/.flake8`, max-line-length 120) |
| `frontend-typecheck` | `npm ci` + `npx tsc --noEmit` |
| `backend-test` | pytest; сервисы pgvector/pg15 и Redis 7 |

Локально то же: `make lint` и `make test` (pytest в контейнере, БД `chainwise_test`).

## Обновление зависимостей

```bash
rm -rf backend/.venv slither/.venv frontend/node_modules
make deps
make up
```

## Локальный запуск без Docker

Полностью host-native workflow не автоматизирован. Практический hybrid-вариант оставляет data services в Compose, отдельно публикует Slither и запускает API/frontend на host.

### Инфра

```bash
docker compose up -d postgres redis qdrant
docker compose run --rm -p 127.0.0.1:8002:8000 slither
```

Порты сервисов данных опубликованы только на `127.0.0.1`, поэтому host-native backend ниже подключается через `localhost`; с других машин они недоступны. Вторая команда остаётся foreground-процессом. Для Slither нужны `backend/.solc` и `slither/.venv`.

### Backend

```bash
cd backend
source .venv/bin/activate
export DATABASE_URL=postgresql+psycopg2://chainwise:change_this_in_prod@localhost:5432/chainwise_db
export REDIS_URL=redis://localhost:6379/0
export QDRANT_URL=http://localhost:6333
export SLITHER_URL=http://localhost:8002
python -m uvicorn app.main:app --reload --port 8001
```

Celery:

```bash
cd backend
source .venv/bin/activate
python -m celery -A app.celery_config worker -l info --concurrency=2
```

Analyzer принимает до двух одновременных задач; если запускаете дополнительные worker-процессы, учитывайте их общую concurrency.

### Frontend

Для быстрой итерации UI (без пересборки Docker-образа):

```bash
cd frontend
NEXT_PUBLIC_API_URL=http://localhost:8001 npm run dev
```

Откройте http://localhost:3000 (dev) против API на :8001. Compose-сборка frontend по-прежнему на :3001 и требует `make up-frontend` после смены `NEXT_PUBLIC_*`.

`NEXT_PUBLIC_API_URL` по умолчанию: `http://localhost:8001`. Кнопка Google: `NEXT_PUBLIC_GOOGLE_CLIENT_ID` (тот же Client ID, что в `.env` как `GOOGLE_CLIENT_ID`). После смены в образе — `make up-frontend`.

## Troubleshooting

### `uvicorn: executable file not found`

В docker-compose используется `python -m uvicorn`, не бинарник `uvicorn` из venv.

### Frontend не видит API

`NEXT_PUBLIC_API_URL` вшивается **на этапе сборки** образа:

```bash
make up-frontend
```

### Backend `status: degraded`

```bash
docker compose ps
docker compose logs postgres redis qdrant slither backend celery-worker
```

### Индексация зависает в `indexing`

Проверь `celery-worker` и `slither`. Ошибка пишется в `GET /contracts/{id}/status` → `last_error`.

### Etherscan 503

Заполни `ETHERSCAN_API_KEY` в `.env` и перезапусти backend.

### RAG-чат 503 / name resolution

Проверь `OPENAI_API_KEY` и доступность настроенного OpenAI-compatible endpoint из backend-контейнера. По умолчанию используются `https://api.openai.com/v1` и модель `gpt-4o-mini`; для другого совместимого сервиса можно задать `OPENAI_BASE_URL` и `OPENAI_MODEL` в `.env`, затем пересоздать backend. Без ключа ответы с найденными чанками не генерируются.

### Offline build падает

Убедись, что `backend/.venv`, `backend/.solc`, `backend/.models`, `slither/.venv` и `frontend/node_modules` существуют и собраны на Linux x86_64.
