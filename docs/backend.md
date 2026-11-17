# Серверная часть

## Стек

- Python 3.12
- FastAPI 0.115+
- SQLAlchemy 2.x + Alembic
- Pydantic Settings v2
- Celery 5 + Redis
- Uvicorn
- FastEmbed, qdrant-client, py-solc-x, xgboost, fpdf2

Зависимости: `backend/requirements.txt` (pip, не Poetry).

## Структура

```
backend/
├── Dockerfile
├── requirements.txt
├── alembic.ini
├── alembic/versions/           # миграции схемы и поколений анализа
├── scripts/
│   ├── prepare-venv.sh
│   ├── prepare_solc.py         # solc 0.8.24 → backend/.solc
│   └── prepare_embeddings.py   # MiniLM ONNX → backend/.models
├── app/
│   ├── main.py                 # FastAPI, /health, CORS, миграции
│   ├── celery_config.py        # Celery и задача indexing.load_contract
│   ├── core/
│   │   ├── config.py
│   │   ├── database.py
│   │   ├── security.py         # JWT, bcrypt
│   │   ├── auth_cookies.py     # HttpOnly access/refresh
│   │   ├── sentry.py
│   │   └── deps.py             # get_db, get_current_user, require_admin
│   ├── models/                 # User, Contract, AnalysisReport, ChatMessage
│   ├── schemas/                # auth, user, contract, chat
│   ├── api/
│   │   ├── auth.py
│   │   ├── contracts.py
│   │   └── history.py
│   ├── services/
│   │   ├── etherscan_loader.py
│   │   ├── upload_handler.py
│   │   ├── contract_service.py
│   │   ├── indexing_worker.py
│   │   ├── solc_compiler.py
│   │   ├── slither_runner.py
│   │   ├── semantic_splitter.py
│   │   ├── qdrant_indexer.py
│   │   ├── indexing_lifecycle.py   # захват попытки, проверка актуальности и сверка
│   │   ├── rag_engine.py          # повторный экспорт для совместимости
│   │   ├── rag/                   # retrieval, prompting, llm_client, cache, engine
│   │   ├── pdf_report.py
│   │   ├── risk_scorer.py
│   │   └── account_service.py
│   ├── models/
│   │   ├── contract.py            # метаданные; содержимое в ContractArtifact
│   │   ├── contract_artifact.py
│   │   ├── contract_status.py     # статусы и переходы
│   │   └── indexing_step.py
│   ├── scripts/
│   │   └── reconcile_indexing.py  # сверка зависших записей
│   └── utils/
│       ├── logging.py
│       ├── rate_limit.py
│       ├── address.py
│       ├── pragma_parser.py
│       └── redis_lock.py
└── tests/
    └── fixtures/Vault.sol      # данные проверки разбиения и цитат
```

История: `GET /history` и `GET /contracts`. PDF: `GET /contracts/{id}/report`. Найденные проблемы: `GET /contracts/{id}/findings`. Исходник для якорей: `GET /contracts/{id}/source`. `risk_model` / `features_version` возвращаются в статусе индексации.

## Эндпоинты

См. [api.md](./api.md). Префикс бизнес-API: `/api/v1`.

## Пайплайн индексации

Celery-задача `indexing.load_contract` (шаги пишутся в `contracts.indexing_step`):

1. `compile` — директива версии и `POST {SLITHER_URL}/compile` в отдельный анализатор → AST
2. `slither` — HTTP `POST {SLITHER_URL}/analyze` в тот же анализатор → отчёт Slither
3. `embed` — разбиение кода по структуре + FastEmbed → Qdrant → `chunk_count`
4. `score` — XGBoost или эвристика → `risk_score`
5. `done` + `status=ready`; при ошибке `failed` и `last_error`

## RAG-чат

`POST /contracts/{id}/chat` (`services/rag/`):

1. Контракт `ready` (иначе `409`)
2. Кеш Redis `llm_cache:v7:{contract_id}:{hash}` с учётом успешного поколения анализа в хеше, затем `analysis_reports` (записи только с IERC20-заглушками игнорируются)
3. Поиск по **листовым контрактам** из AST (не библиотеки, интерфейсы и базовые контракты OpenZeppelin). Небольшой исходник без листового контракта — целиком. Обзорный вопрос использует основные найденные проблемы и затронутые функции. Для остальных вопросов применяется гибридный поиск BM25 и векторов.
4. API Chat Completions, совместимый с OpenAI, вызывается через `httpx`; `OPENAI_API_KEY` задаёт ключ, `OPENAI_BASE_URL` и `OPENAI_MODEL` при необходимости переопределяют адрес сервиса и модель. По умолчанию используются OpenAI и GPT-4o-mini.
5. Цитаты только из найденных `.sol` (`file_path` из индекса, выдуманные пути модели отбрасываются)
6. JSON или SSE (`stream=true`): `citations` → `token`* → `meta` → `[DONE]`
7. Успешный ответ пишется в `chat_messages` (вопрос + ответ). `GET /contracts/{id}/chat` отдаёт ленту текущего пользователя. История в промпт LLM не входит.

Без `OPENAI_API_KEY` при наличии чанков — `503`. Пустой индекс — фиксированный ответ без вызова LLM.

Идемпотентность: Redis хранит случайный токен владельца с TTL 3600 секунд. API и Celery удаляют блокировку только атомарным сравнением токена. Состояние и UUID попытки проверяются в PostgreSQL под строковой блокировкой перед каждой записью. Устаревшая попытка не может перезаписать новое поколение. Общий срок выполнения задачи Celery не задан; автоматические повторы задач не включены. Подробности — в [жизненном цикле фоновых задач](./background-jobs.md).

## PDF-отчёт

`GET /contracts/{id}/report` (`pdf_report.py`, fpdf2): контракт `ready` или `loaded`, иначе `409`. В PDF — имя, адрес, сеть, дата, оценка A–D и проверки Slither с серьёзностью, уверенностью и расположением; исходный код не включается.

## Отдельный сервис анализа

Отдельный сервис `slither/` (FastAPI анализатор). Он запускает только разрешённые заранее установленные `solc` и Slither; сервер и Celery не исполняют эти бинарники напрямую.

| Метод | Путь | Назначение |
|---|---|---|
| GET | `/health` | Проверка состояния Compose |
| GET | `/solc/versions` | Установленные разрешённые версии solc |
| POST | `/compile` | `{files, solc_version}` → `{solc_version, sources}` с AST |
| POST | `/analyze` | `{files, solc_version}` → JSON-отчёт Slither |

Бинарники solc монтируются из `backend/.solc`.

## Конвенции

- Точка входа: `app/main.py`; обработчик: `app.celery_config`
- Конфиг: `app/core/config.py` через `get_settings()`
- Роутеры — в `app/api/`, бизнес-логика — в `app/services/`
- Миграции: Alembic, `upgrade head` при старте сервера
- Логи: `python-json-logger`, JSON в stdout; исходник и ответы LLM в сообщениях журналов скрываются
- Sentry: для localhost **не включать**. Пустой `SENTRY_DSN` — интеграция отключена. Как завести проект и DSN — [настройка Sentry](./environment.md#sentry)
- Ограничение частоты запросов через Redis для анализа по адресу, загрузки файлов и чата
- Секреты только в `.env`

## CORS

Разрешены источники запросов:

- `http://localhost:3000` / `http://127.0.0.1:3000` (локальный `npm run dev`)
- `http://localhost:3001` / `http://127.0.0.1:3001` (интерфейс Docker)

## Тесты

```bash
make test
# docker compose exec backend python -m pytest tests/ -v
make lint
# docker compose exec backend python -m flake8 app tests scripts
```

Тесты охватывают аутентификацию, загрузку, компиляцию, анализатор, разбиение кода, Qdrant, обработку задач, конкурентный доступ PostgreSQL, кеширование по поколениям, RAG, PDF и скрытие чувствительных данных в журналах. Порог покрытия выбранных модулей `pragma_parser` и `risk_scorer` — 80%; он не является общим покрытием проекта. Автоматические проверки описаны в [установке](./setup.md#ci).
