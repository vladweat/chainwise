# Переменные окружения

Файл: `.env` (скопировать из `.env.example`).

**Секреты не коммитить в git.**

## Текущий `.env.example`

```env
POSTGRES_USER=chainwise
POSTGRES_PASSWORD=change_this_in_prod
POSTGRES_DB=chainwise_db
DATABASE_URL=postgresql+psycopg2://chainwise:change_this_in_prod@postgres:5432/chainwise_db
REDIS_URL=redis://redis:6379/0
QDRANT_URL=http://qdrant:6333
QDRANT_API_KEY=
ENVIRONMENT=development
LOG_LEVEL=INFO
SECRET_KEY=change_this_secret_key_in_prod
ALGORITHM=HS256
ACCESS_TOKEN_EXPIRE_MINUTES=1440
GOOGLE_CLIENT_ID=
CELERY_BROKER_URL=redis://redis:6379/0
CELERY_RESULT_BACKEND=redis://redis:6379/0
SLITHER_URL=http://slither:8000
ETHERSCAN_API_KEY=
RISK_MODEL_PATH=/app/ml_models/risk_xgb_model.pkl
OPENAI_API_KEY=
# Optional OpenAI-compatible gateway configuration.
# OPENAI_BASE_URL=https://api.example.com/v1
# OPENAI_MODEL=your-model-name
SENTRY_DSN=
# Локально можно оставить пустым; подробности — раздел Sentry ниже.
RATE_LIMIT_ANALYZE_PER_MINUTE=10
RATE_LIMIT_CHAT_PER_MINUTE=30
QUOTA_MAX_CONTRACTS=20
QUOTA_ANALYZE_PER_DAY=20
QUOTA_CHAT_PER_DAY=100
REFRESH_TOKEN_EXPIRE_MINUTES=10080
COOKIE_SAMESITE=lax
COOKIE_SECURE=false
CORS_ORIGINS=http://localhost:3000,http://127.0.0.1:3000,http://localhost:3001,http://127.0.0.1:3001
NEXT_PUBLIC_API_URL=http://localhost:8001
```

`OPENAI_API_KEY` нужен для RAG-чата (`POST /contracts/{id}/chat`). Без ключа чат отдаёт `503`, если нет попадания в кеш и в Qdrant есть чанки.

Стандартная конфигурация использует `https://api.openai.com/v1` и модель `gpt-4o-mini`. Для других OpenAI-compatible сервисов задайте `OPENAI_BASE_URL` и `OPENAI_MODEL`.

Rate limit: Redis `rl:analyze:{user_id}` / `rl:chat:{user_id}` (окно 60 с). Дневные квоты: `quota:*` + `QUOTA_MAX_CONTRACTS`. JWT для SPA — HttpOnly cookies `cw_access` / `cw_refresh`; Bearer по-прежнему принимается (Swagger, тесты). Cookie-мутации требуют `X-Requested-With`.

## Обязательность и defaults

Все поля `Settings` имеют code defaults, поэтому Pydantic формально не требует ни одной переменной. Для рабочего Compose значения подключения переопределяются в `docker-compose.yml`. Для конкретных функций:

| Переменная | Когда фактически нужна | Default в code | Использует |
|---|---|---|---|
| `DATABASE_URL` | всегда | localhost development URL | API, worker |
| `REDIS_URL` | auth refresh, limits, locks/cache | `redis://localhost:6379/0` | API, worker |
| `CELERY_BROKER_URL` | индексация | Redis localhost | API, worker |
| `CELERY_RESULT_BACKEND` | Celery results | Redis localhost | worker |
| `QDRANT_URL` | индексация и RAG | `http://localhost:6333` | API, worker |
| `SLITHER_URL` | индексация | `http://slither:8000` | worker, health |
| `SECRET_KEY` | всегда для auth | небезопасный development placeholder | API |
| `ETHERSCAN_API_KEY` | analyze по адресу | пусто | API |
| `OPENAI_API_KEY` | cache miss в RAG при наличии chunks | пусто | API |
| `GOOGLE_CLIENT_ID` | Google login | пусто | API, frontend build |
| `SENTRY_DSN` | только Sentry | пусто | API, worker |

Пустые optional keys отключают соответствующую интеграцию. При `ENVIRONMENT=production` Pydantic Settings проверяет конфигурацию до создания API/worker: сильные неплейсхолдерные `SECRET_KEY` и пароль в PostgreSQL URL, `COOKIE_SECURE=true`, допустимый `COOKIE_SAMESITE`, HTTPS CORS/API origins без localhost и access-token lifetime 5–60 минут. Ошибки redacted и содержат имена параметров и ожидаемые значения без самих секретов. Production Compose overlay также убирает `--reload` и требует настройки production переменных.

Политика access token: 5–60 минут в production, рекомендуется 15 минут. Development default `1440` минут не меняется, а в production должен быть задан явно. TTL refresh-cookie (по умолчанию 7 дней) этой политикой не меняется.

## Sentry

**Для локального запуска опционален.** Пустой `SENTRY_DSN` — SDK не стартует, ошибки остаются в логах контейнеров (`make logs`).

Включать, если нужен облачный трекинг сбоев API, Celery, solc, Slither, LLM:

1. Проект Python / FastAPI на [sentry.io](https://sentry.io).
2. DSN в `.env` (не коммитить): `SENTRY_DSN=https://...@....ingest.sentry.io/...`
3. `sentry-sdk` в `requirements.txt`; после `pip install` перезапустить **backend и celery-worker**.

В Issues — тег `pipeline_component`: `solc` | `slither` | `llm` | `indexing`. Тела запросов, исходник, ответы LLM, `Authorization` и cookies не отправляются (`send_default_pii=False`).

## Справочник

### Базы данных

| Переменная | Пример | Описание |
|---|---|---|
| `POSTGRES_USER` | `chainwise` | Пользователь PostgreSQL |
| `POSTGRES_PASSWORD` | development placeholder | Production требует 32+ неплейсхолдерных символов и не менее 12 различных |
| `POSTGRES_DB` | `chainwise_db` | Имя БД |
| `DATABASE_URL` | `postgresql+psycopg2://...@postgres:5432/...` | SQLAlchemy |

### Кеш и брокер

| Переменная | Пример | Описание |
|---|---|---|
| `REDIS_URL` | `redis://redis:6379/0` | Redis |
| `CELERY_BROKER_URL` | `redis://redis:6379/0` | Celery broker |
| `CELERY_RESULT_BACKEND` | `redis://redis:6379/0` | Celery results |

### Векторная БД и эмбеддинги

| Переменная | Пример | Описание |
|---|---|---|
| `QDRANT_URL` | `http://qdrant:6333` | Qdrant HTTP API |
| `QDRANT_API_KEY` | | Опционально |
| `EMBEDDING_MODEL_PATH` | `/app/.models` | Кеш FastEmbed (задаётся в Compose) |

### Анализ

| Переменная | Пример | Описание |
|---|---|---|
| `SLITHER_URL` | `http://slither:8000` | Sidecar Slither |
| `RISK_MODEL_PATH` | `/app/ml_models/risk_xgb_model.pkl` | XGBoost pickle |

### Внешние API

| Переменная | Описание |
|---|---|
| `ETHERSCAN_API_KEY` | Загрузка по адресу (Etherscan / BscScan / Abscan) |
| `GOOGLE_CLIENT_ID` | Google OAuth 2.0 |
| `OPENAI_API_KEY` | Ключ для RAG-чата (OpenAI или совместимого сервиса) |
| `OPENAI_BASE_URL` | Опционально. Base URL OpenAI-compatible API; по умолчанию выбирается по ключу |
| `OPENAI_MODEL` | Опционально. Модель API; по умолчанию выбирается по ключу |
| `SENTRY_DSN` | Опционально. Пусто — без Sentry (норма для localhost). См. [Sentry](#sentry) |

### Безопасность

| Переменная | Пример | Описание |
|---|---|---|
| `SECRET_KEY` | `openssl rand -hex 32` | JWT signing key |
| `ALGORITHM` | `HS256` | JWT algorithm |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | `1440` | Development default: 24ч; production: 5–60 минут, рекомендуется 15 |
| `REFRESH_TOKEN_EXPIRE_MINUTES` | `10080` | TTL refresh-cookie (7 дней) |
| `COOKIE_SAMESITE` | `lax` | `lax` для same-site; `none` + `COOKIE_SECURE=true` для cross-site SPA |
| `COOKIE_SECURE` | `false` | Development default; production требует `true` |
| `CORS_ORIGINS` | `http://localhost:3001,...` | Development allowlist; production требует HTTPS origins без localhost/loopback |
| `RATE_LIMIT_ANALYZE_PER_MINUTE` | `10` | Лимит `/analyze` и upload |
| `RATE_LIMIT_CHAT_PER_MINUTE` | `30` | Лимит `POST /chat` |
| `QUOTA_MAX_CONTRACTS` | `20` | Макс. контрактов на пользователя (`0` = off) |
| `QUOTA_ANALYZE_PER_DAY` | `20` | Дневной лимит analyze/upload |
| `QUOTA_CHAT_PER_DAY` | `100` | Дневной лимит chat |

### Приложение

| Переменная | Пример | Описание |
|---|---|---|
| `ENVIRONMENT` | `development` | `development` / `production` |
| `LOG_LEVEL` | `INFO` | Уровень логирования |

### Frontend (build-time)

| Переменная | Пример | Описание |
|---|---|---|
| `NEXT_PUBLIC_API_URL` | `http://localhost:8001` | URL backend для браузера; production требует HTTPS origin без localhost/loopback |
| `NEXT_PUBLIC_GOOGLE_CLIENT_ID` | тот же, что `GOOGLE_CLIENT_ID` | Кнопка Google на `/login` |

> `NEXT_PUBLIC_*` вшивается при `npm run build`. Compose передаёт `GOOGLE_CLIENT_ID` как `NEXT_PUBLIC_GOOGLE_CLIENT_ID`. `NEXT_PUBLIC_API_URL` по умолчанию указывает на локальный backend; production требует HTTPS origin без localhost. Для HTTPS reverse proxy задайте адрес до сборки frontend через production Compose overlay. Для `npm run dev` задайте переменную в окружении.

`POSTGRES_*` читает container image PostgreSQL и Compose interpolation; они не являются полями backend `Settings`. Proxy variables и offline flags читаются библиотеками/сервисными модулями или задаются Compose напрямую. Компиляторы solc доступны только analyzer sidecar через read-only mount.

В Compose также: `HF_HUB_OFFLINE=1`, `TRANSFORMERS_OFFLINE=1`, `LD_LIBRARY_PATH=/usr/local/lib`.

## Docker vs localhost

| Переменная | В Docker | Локально (без Docker) |
|---|---|---|
| `DATABASE_URL` | `@postgres:5432` | `@localhost:5432` |
| `REDIS_URL` | `redis://redis:6379/0` | `redis://localhost:6379/0` |
| `QDRANT_URL` | `http://qdrant:6333` | `http://localhost:6333` |
| `SLITHER_URL` | `http://slither:8000` | `http://127.0.0.1:8000`; нужен отдельно доступный analyzer, Compose-sidecar не публикуется на host |

## Генерация секретов

```bash
openssl rand -hex 32   # SECRET_KEY
```

Не выводите итоговое значение в CI logs и не храните его в shell history production-host. Репозиторий не интегрирован с secret manager; способ доставки секретов должен определить deployment environment.
