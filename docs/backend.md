# Backend

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
├── alembic/versions/           # 001 initial schema … 009 artifacts/indexing step
├── scripts/
│   ├── prepare-venv.sh
│   ├── prepare_solc.py         # solc 0.8.24 → backend/.solc
│   └── prepare_embeddings.py   # MiniLM ONNX → backend/.models
├── app/
│   ├── main.py                 # FastAPI, /health, CORS, миграции
│   ├── celery_config.py        # Celery + task indexing.load_contract
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
│   │   ├── indexing_lifecycle.py   # attempt claim, fencing, stale reconciliation
│   │   ├── rag_engine.py          # thin re-export
│   │   ├── rag/                   # retrieval, prompting, llm_client, cache, engine
│   │   ├── pdf_report.py
│   │   ├── risk_scorer.py
│   │   └── account_service.py
│   ├── models/
│   │   ├── contract.py            # metadata; blobs via ContractArtifact
│   │   ├── contract_artifact.py
│   │   ├── contract_status.py     # status enum + transitions
│   │   └── indexing_step.py
│   ├── scripts/
│   │   └── reconcile_indexing.py  # deterministic stale-row recovery
│   └── utils/
│       ├── logging.py
│       ├── rate_limit.py
│       ├── address.py
│       ├── pragma_parser.py
│       └── redis_lock.py
└── tests/
    └── fixtures/Vault.sol      # фикстура split → citations
```

История: `GET /history` и `GET /contracts`. PDF: `GET /contracts/{id}/report`. Findings: `GET /contracts/{id}/findings`. Исходник для якорей: `GET /contracts/{id}/source`. `risk_model` / `features_version` возвращаются в статусе индексации.

## Эндпоинты

См. [api.md](./api.md). Префикс бизнес-API: `/api/v1`.

## Пайплайн индексации

Celery-задача `indexing.load_contract` (шаги пишутся в `contracts.indexing_step`):

1. `compile` — pragma + `POST {SLITHER_URL}/compile` to isolated analyzer → artifact.ast
2. `slither` — HTTP `POST {SLITHER_URL}/analyze` to the same analyzer → artifact.slither_report
3. `embed` — semantic split + FastEmbed → Qdrant → `chunk_count`
4. `score` — XGBoost / heuristic → `risk_score`
5. `done` + `status=ready`; при ошибке `failed` и `last_error`

## RAG-чат

`POST /contracts/{id}/chat` (`services/rag/`):

1. Контракт `ready` (иначе `409`)
2. Кеш Redis `llm_cache:v6:{contract_id}:{hash}`, затем `analysis_reports` (записи только с IERC20-заглушками игнорируются)
3. Поиск по **листовым контрактам** из AST (не OpenZeppelin library/interface/базы). Небольшой исходник без листового контракта — целиком. Overview-вопрос — top findings + затронутые функции (не все чанки). Иначе hybrid BM25+vector.
4. OpenAI-compatible Chat Completions API вызывается через `httpx`; `OPENAI_API_KEY` задаёт ключ, `OPENAI_BASE_URL` и `OPENAI_MODEL` при необходимости переопределяют endpoint и модель. По умолчанию используются OpenAI и GPT-4o-mini.
5. Цитаты только из retrieved `.sol` (`file_path` из индекса, выдуманные пути модели отбрасываются)
6. JSON или SSE (`stream=true`): `citations` → `token`* → `meta` → `[DONE]`
7. Успешный ответ пишется в `chat_messages` (вопрос + ответ). `GET /contracts/{id}/chat` отдаёт ленту текущего пользователя. История в промпт LLM не входит.

Без `OPENAI_API_KEY` при наличии чанков — `503`. Пустой индекс — фиксированный ответ без вызова LLM.

Идемпотентность: Redis-ключ `indexing_lock:{user_id}:{chain_id}:{address}` (TTL 1 час) хранит случайный owner token; API и Celery удаляют его только атомарным compare-and-delete. Token передаётся только в Celery и не входит в публичные схемы. Повторный analyze для своего `ready`/`loaded` возвращает существующую запись без новой задачи. Пока DB-статус `indexing` — `409`; Redis lock не заменяет DB-контроль. Celery task time limit не задан, поэтому TTL не гарантирует, что задача завершится до его истечения; fencing для DB-записей не реализован. Подробности — [жизненный цикл фоновых задач](./background-jobs.md#lock-и-повторные-запросы).

Lock ограничивает дубли, но не обеспечивает exactly-once: lease renewal и DB fencing отсутствуют. Retry Celery явно не настроен. Полный lifecycle и failure modes — [background-jobs.md](./background-jobs.md).

## PDF-отчёт

`GET /contracts/{id}/report` (`pdf_report.py`, fpdf2): контракт `ready` или `loaded`, иначе `409`. В PDF — имя, адрес, chain, дата, risk A–D и детекторы Slither (check / impact / confidence, короткое location); исходный код не включается.

## Analyzer sidecar

Отдельный сервис `slither/` (FastAPI analyzer). Он запускает только allowlisted preinstalled `solc` и Slither; backend/Celery не исполняют эти бинарники напрямую.

| Метод | Путь | Назначение |
|---|---|---|
| GET | `/health` | Healthcheck Compose |
| GET | `/solc/versions` | Установленные allowlisted версии solc |
| POST | `/compile` | `{files, solc_version}` → `{solc_version, sources}` с AST |
| POST | `/analyze` | `{files, solc_version}` → JSON-отчёт Slither |

Бинарники solc монтируются из `backend/.solc`.

## Конвенции

- Точка входа: `app/main.py`; воркер: `app.celery_config`
- Конфиг: `app/core/config.py` через `get_settings()`
- Роутеры — в `app/api/`, бизнес-логика — в `app/services/`
- Миграции: Alembic, `upgrade head` при старте backend
- Логи: `python-json-logger`, JSON в stdout; исходник и ответы LLM в extra/message редактируются
- Sentry: для localhost **не включать**. Пустой `SENTRY_DSN` — no-op. Как завести проект и DSN — [environment.md § Sentry](./environment.md#sentry)
- Rate limit: Redis на `POST /analyze`, upload и `POST /chat`
- Секреты только в `.env`

## CORS

Разрешены origins:

- `http://localhost:3000` / `http://127.0.0.1:3000` (локальный `npm run dev`)
- `http://localhost:3001` / `http://127.0.0.1:3001` (Docker frontend)

## Тесты

```bash
make test
# docker compose exec backend python -m pytest tests/ -v
make lint
# docker compose exec backend python -m flake8 app tests scripts
```

Покрытие: health, auth (cookies + refresh), etherscan, upload, pragma, solc, slither runner, splitter, qdrant indexer, indexing worker, contracts API, risk scorer, RAG, PDF, rate limit, redaction логов. **pytest-cov** для `pragma_parser` и `risk_scorer` >80% (`make test`, CI).

CI: `.github/workflows/ci.yml` на каждый push и pull request — flake8, `npx tsc --noEmit`, pytest (сервисы Postgres + Redis). RAG: выдуманный `file_path` отбрасывается; zip с OpenZeppelin не попадает в контекст.
