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
# Необязательная настройка сервиса, совместимого с OpenAI.
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

Для обычного ключа используются `https://api.openai.com/v1` и `gpt-4o-mini`. Код также распознаёт ключи поддерживаемого шлюза и выбирает его адрес и модель. Чтобы задать сервис однозначно, укажите `OPENAI_BASE_URL` и `OPENAI_MODEL`: явные значения имеют приоритет.

Ограничение частоты запросов: Redis `rl:analyze:{user_id}` / `rl:chat:{user_id}` (окно 60 с). Дневные квоты: `quota:*` + `QUOTA_MAX_CONTRACTS`. JWT для браузерного приложения — cookie с HttpOnly `cw_access` / `cw_refresh`; Bearer по-прежнему принимается (Swagger, тесты). Изменяющие запросы с cookie требуют `X-Requested-With`.

## Обязательные параметры и значения по умолчанию

Все поля `Settings` имеют значения в коде по умолчанию, поэтому Pydantic формально не требует ни одной переменной. Для рабочего Compose значения подключения переопределяются в `docker-compose.yml`. Для конкретных функций:

| Переменная | Когда фактически нужна | По умолчанию в коде | Использует |
|---|---|---|---|
| `DATABASE_URL` | всегда | локальный адрес для разработки | API, обработчик |
| `REDIS_URL` | обновление сеансов, квоты, блокировки и кеш | `redis://localhost:6379/0` | API, обработчик |
| `CELERY_BROKER_URL` | индексация | локальный Redis | API, обработчик |
| `CELERY_RESULT_BACKEND` | результаты задач Celery | локальный Redis | обработчик |
| `QDRANT_URL` | индексация и RAG | `http://localhost:6333` | API, обработчик |
| `SLITHER_URL` | индексация | `http://slither:8000` | обработчик, проверка состояния |
| `SECRET_KEY` | всегда для аутентификации | небезопасная заглушка для разработки | API |
| `ETHERSCAN_API_KEY` | анализ по адресу | пусто | API |
| `OPENAI_API_KEY` | промах кеша в RAG при наличии фрагментов | пусто | API |
| `GOOGLE_CLIENT_ID` | вход через Google | пусто | API, сборка интерфейса |
| `SENTRY_DSN` | только Sentry | пусто | API, обработчик |

Пустые необязательные ключи отключают соответствующие интеграции. При `ENVIRONMENT=production` настройки проверяются до запуска API и обработчика: сильные секреты и пароль PostgreSQL, `COOKIE_SECURE=true`, допустимый `COOKIE_SAMESITE`, адреса HTTPS без localhost и локальных IP, срок токена доступа 5–60 минут. Сообщения называют параметр и ожидаемое условие, не раскрывая секрет.

В рабочей среде рекомендуется срок токена доступа 15 минут; его нужно задать явно. В разработке значение по умолчанию — 1440 минут. Срок cookie обновления по умолчанию — 7 дней.

## Sentry

**Для локального запуска опционален.** Пустой `SENTRY_DSN` — SDK не стартует, ошибки остаются в логах контейнеров (`make logs`).

Включать, если нужен облачный трекинг сбоев API, Celery, solc, Slither, LLM:

1. Проект Python / FastAPI на [sentry.io](https://sentry.io).
2. DSN в `.env` (не коммитить): `SENTRY_DSN=https://...@....ingest.sentry.io/...`
3. `sentry-sdk` в `requirements.txt`; после `pip install` перезапустить **сервер и обработчик Celery**.

В списке ошибок — тег `pipeline_component`: `solc` | `slither` | `llm` | `indexing`. Тела запросов, исходник, ответы LLM, `Authorization` и cookie не отправляются (`send_default_pii=False`).

## Справочник

### Базы данных

| Переменная | Пример | Описание |
|---|---|---|
| `POSTGRES_USER` | `chainwise` | Пользователь PostgreSQL |
| `POSTGRES_PASSWORD` | заглушка для разработки | В рабочей среде нужен пароль от 32 символов с не менее 12 различными символами, не являющийся заглушкой |
| `POSTGRES_DB` | `chainwise_db` | Имя БД |
| `DATABASE_URL` | `postgresql+psycopg2://...@postgres:5432/...` | SQLAlchemy |

### Кеш и брокер

| Переменная | Пример | Описание |
|---|---|---|
| `REDIS_URL` | `redis://redis:6379/0` | Redis |
| `CELERY_BROKER_URL` | `redis://redis:6379/0` | Брокер Celery |
| `CELERY_RESULT_BACKEND` | `redis://redis:6379/0` | результаты задач Celery |

### Векторная БД и эмбеддинги

| Переменная | Пример | Описание |
|---|---|---|
| `QDRANT_URL` | `http://qdrant:6333` | Qdrant HTTP API |
| `QDRANT_API_KEY` | | Опционально |
| `EMBEDDING_MODEL_PATH` | `/app/.models` | Кеш FastEmbed (задаётся в Compose) |

### Анализ

| Переменная | Пример | Описание |
|---|---|---|
| `SLITHER_URL` | `http://slither:8000` | Сервис анализа Slither |
| `RISK_MODEL_PATH` | `/app/ml_models/risk_xgb_model.pkl` | Файл модели XGBoost в формате pickle |

### Внешние API

| Переменная | Описание |
|---|---|
| `ETHERSCAN_API_KEY` | Загрузка по адресу (Etherscan / BscScan / Abscan) |
| `GOOGLE_CLIENT_ID` | Google OAuth 2.0 |
| `OPENAI_API_KEY` | Ключ для RAG-чата (OpenAI или совместимого сервиса) |
| `OPENAI_BASE_URL` | Опционально. Базовый адрес API; явное значение имеет приоритет над автоматическим выбором по ключу |
| `OPENAI_MODEL` | Опционально. Модель API; явное значение имеет приоритет над автоматическим выбором по ключу |
| `SENTRY_DSN` | Опционально. Пусто — без Sentry (норма для localhost). См. [Sentry](#sentry) |

### Безопасность

| Переменная | Пример | Описание |
|---|---|---|
| `SECRET_KEY` | `openssl rand -hex 32` | Ключ подписи JWT |
| `ALGORITHM` | `HS256` | Алгоритм JWT |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | `1440` | В разработке по умолчанию: 24ч; рабочая среда: 5–60 минут, рекомендуется 15 |
| `REFRESH_TOKEN_EXPIRE_MINUTES` | `10080` | Срок действия cookie обновления (7 дней) |
| `COOKIE_SAMESITE` | `lax` | `lax` для запросов в пределах одного сайта; `none` + `COOKIE_SECURE=true` для запросов интерфейса между сайтами |
| `COOKIE_SECURE` | `false` | В разработке по умолчанию; рабочая среда требует `true` |
| `CORS_ORIGINS` | `http://localhost:3001,...` | Список разрешённых адресов для разработки; рабочая среда требует адреса HTTPS без localhost и локальных IP |
| `RATE_LIMIT_ANALYZE_PER_MINUTE` | `10` | Лимит анализа по адресу и загрузки |
| `RATE_LIMIT_CHAT_PER_MINUTE` | `30` | Лимит `POST /chat` |
| `QUOTA_MAX_CONTRACTS` | `20` | Макс. контрактов на пользователя (`0` = отключено) |
| `QUOTA_ANALYZE_PER_DAY` | `20` | Дневной лимит анализа и загрузок |
| `QUOTA_CHAT_PER_DAY` | `100` | Дневной лимит чата |

### Приложение

| Переменная | Пример | Описание |
|---|---|---|
| `ENVIRONMENT` | `development` | `development` / `production` |
| `LOG_LEVEL` | `INFO` | Уровень логирования |

### Параметры сборки интерфейса

| Переменная | Пример | Описание |
|---|---|---|
| `NEXT_PUBLIC_API_URL` | `http://localhost:8001` | URL сервера для браузера; рабочая среда требует адрес HTTPS без localhost и локальных IP |
| `NEXT_PUBLIC_GOOGLE_CLIENT_ID` | тот же, что `GOOGLE_CLIENT_ID` | Кнопка Google на `/login` |

> `NEXT_PUBLIC_*` вшивается при `npm run build`. Compose передаёт `GOOGLE_CLIENT_ID` как `NEXT_PUBLIC_GOOGLE_CLIENT_ID`. `NEXT_PUBLIC_API_URL` по умолчанию указывает на локальный сервер; рабочая среда требует адрес HTTPS без localhost. Для обратного прокси HTTPS задайте адрес до сборки интерфейса через дополнительную конфигурацию Compose для рабочей среды. Для `npm run dev` задайте переменную в окружении.

`POSTGRES_*` читает образ PostgreSQL и механизм подстановки Compose; они не являются полями серверных `Settings`. Параметры прокси и запрета сети читаются библиотеками/сервисными модулями или задаются Compose напрямую. Компиляторы solc доступны только в отдельном сервисе анализа через каталог только для чтения.

В Compose также: `HF_HUB_OFFLINE=1`, `TRANSFORMERS_OFFLINE=1`, `LD_LIBRARY_PATH=/usr/local/lib`.

## Docker и локальный запуск

| Переменная | В Docker | Локально (без Docker) |
|---|---|---|
| `DATABASE_URL` | `@postgres:5432` | `@localhost:5432` |
| `REDIS_URL` | `redis://redis:6379/0` | `redis://localhost:6379/0` |
| `QDRANT_URL` | `http://qdrant:6333` | `http://localhost:6333` |
| `SLITHER_URL` | `http://slither:8000` | `http://127.0.0.1:8000`; нужен отдельно доступный анализатор, сервис анализа Compose не публикует порт хоста |

## Генерация секретов

```bash
openssl rand -hex 32   # SECRET_KEY
```

Не выводите итоговое значение в журналах автоматических проверок и не храните его в истории оболочки рабочего хоста. Репозиторий не интегрирован с системой управления секретами; способ доставки секретов должен определить окружение развёртывания.
