# API

Префикс бизнес-эндпоинтов: `/api/v1`.

Интерактивно: http://localhost:8001/docs · ReDoc: http://localhost:8001/redoc

Runtime OpenAPI (`GET /openapi.json`) генерируется FastAPI из routes и Pydantic schemas и является источником истины для полных JSON contracts. Этот документ фиксирует поведение и ограничения; при изменении route/schema должны обновляться тесты и соответствующий раздел здесь.

## Инфраструктура

| Метод | Путь | Auth | Ответ | Описание |
|---|---|---|---|---|
| GET | `/` | — | `{service, docs}` | Информация о сервисе |
| GET | `/health` | — | `HealthResponse` | Smoke test |

### HealthResponse

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

`status`: `ok` — все проверки успешны, `degraded` — хотя бы одна `error`.

---

## Auth

| Метод | Путь | Тело | Ответ |
|---|---|---|---|
| POST | `/api/v1/auth/register` | `{email, password}` | `201` `{access_token, token_type}` + cookies |
| POST | `/api/v1/auth/login` | `{email, password}` | `{access_token, token_type}` + cookies |
| POST | `/api/v1/auth/google` | `{id_token}` | `{access_token, token_type}` + cookies |
| POST | `/api/v1/auth/refresh` | cookie `cw_refresh` | новый access + ротация refresh |
| POST | `/api/v1/auth/logout` | cookies + `X-Requested-With` | `204`, cookies сброшены |
| GET | `/api/v1/auth/me` | Bearer JWT **или** cookie `cw_access` | `UserResponse` |
| DELETE | `/api/v1/auth/me` | JWT / cookie | `204`, user + contracts CASCADE, Qdrant cleanup |

Пароль при регистрации: 8–128 символов. `token_type` всегда `bearer`. Access JWT дублируется в HttpOnly cookie `cw_access`; refresh — `cw_refresh` (Redis `refresh:{jti}`). UI не кладёт JWT в `localStorage`. Bearer остаётся для Swagger и скриптов.

Мутирующие запросы **только с cookie-сессией** (без `Authorization: Bearer`) требуют заголовок `X-Requested-With` (CSRF). Bearer-запросы и публичный register/login — без него.

`UserResponse`: `{id, email, role, is_active, created_at}`.

Google OAuth: нужен `GOOGLE_CLIENT_ID`. Без него — `503`. На `/login` кнопка Google Identity Services, если задан `NEXT_PUBLIC_GOOGLE_CLIENT_ID`.

Контракты **приватны per-user**: чужой `contract_id` → `404`. Один адрес у двух пользователей — две независимые копии. Квоты (без оплаты): `QUOTA_MAX_CONTRACTS`, `QUOTA_ANALYZE_PER_DAY`, `QUOTA_CHAT_PER_DAY` → `429`.

---

## Contracts (реализовано)

Все методы требуют JWT.

| Метод | Путь | Тело | Ответ |
|---|---|---|---|
| POST | `/api/v1/contracts/analyze` | `{address, chain_id}` | `202` `{task_id, contract_id, status}` |
| POST | `/api/v1/contracts/analyze/upload` | `multipart: file, chain_id?` | `202` `{task_id, contract_id, status}` |
| GET | `/api/v1/contracts` | — | до 50 последних `[ContractStatusResponse]` |
| DELETE | `/api/v1/contracts/{id}` | — | `204`, контракт + CASCADE чат/отчёты, Qdrant cleanup |
| GET | `/api/v1/contracts/{id}/status` | — | `ContractStatusResponse` (+ `indexing_step`) |
| GET | `/api/v1/contracts/{id}/chat` | — | `[ChatMessage]` |
| POST | `/api/v1/contracts/{id}/chat` | `{question, stream?}` | JSON или **SSE stream** |
| GET | `/api/v1/contracts/{id}/report` | — | `application/pdf` |
| GET | `/api/v1/contracts/{id}/findings` | — | `{contract_id, findings[]}` |
| GET | `/api/v1/contracts/{id}/source` | — | `{contract_id, files: [{path, content}]}` |
| GET | `/api/v1/history` | — | то же, что `GET /contracts` |

### Analyze

- Адрес: `0x` + 40 hex. Сети: `1`, `56`, `11124`.
- Upload: `.sol` или `.zip`, до 10 MB. `chain_id` по умолчанию `0`; адрес — SHA256 от `chain_id:source`.
- Повторный запрос при `ready`/`loaded` возвращает существующий контракт без новой задачи.
- Пока идёт индексация — `409`.
- Rate limit Redis: `429` + `Retry-After` (`RATE_LIMIT_ANALYZE_PER_MINUTE`). Upload считается в тот же лимит.

### List и pagination

`GET /api/v1/contracts` и alias `GET /api/v1/history` не принимают pagination, filtering или sorting parameters. Service сортирует по `contracts.id DESC` и возвращает максимум 50 записей. Для admin это последние 50 контрактов всех пользователей; для обычного пользователя — только его записи.

### Report

`GET /api/v1/contracts/{id}/report` — PDF. Контракт `ready` или `loaded`, иначе `409`. В файле: имя, адрес, chain, дата, risk A–D, список Slither detectors (check / impact / confidence, короткое location). Полный исходник не включается.

### ContractStatusResponse

```json
{
  "contract_id": 1,
  "address": "0x...",
  "chain_id": 1,
  "status": "ready",
  "risk_score": null,
  "compiler_version": "0.8.24",
  "name": "MyToken",
  "chunk_count": 12,
  "last_error": null,
  "created_at": "2026-09-08T12:00:00"
}
```

`status`: `pending` | `indexing` | `loaded` | `ready` | `failed`.

`risk_score`: `A` | `B` | `C` | `D` после успешной индексации; `null` пока идёт пайплайн. `risk_model`: `xgboost` | `heuristic`. `features_version`: сейчас `v1`. Чужой контракт для не-admin — `404`.

### Findings

`GET /api/v1/contracts/{id}/findings` — JWT. Список детекторов Slither: `check`, `impact`, `confidence`, `location`, `file_path`, `start_line`, `end_line`, `description`.

`GET /api/v1/contracts/{id}/source` — JWT. Файлы исходника (`// File: path` → несколько записей) для якоря findings в UI, не редактор. PDF по-прежнему без исходника.

Фильтры High/Medium/Low и reentrancy / delegatecall / selfdestruct — на фронте.

### Chat

Контракт должен быть `ready`. Иначе `409`. Чужой / нет — `404`. Без `OPENAI_API_KEY` и без попадания в кеш (если есть чанки) — `503`. Слишком частые запросы — `429` (`RATE_LIMIT_CHAT_PER_MINUTE`).

| Метод | Путь | Тело | Ответ |
|---|---|---|---|
| GET | `/api/v1/contracts/{id}/chat` | — | `[ChatMessage]` |
| POST | `/api/v1/contracts/{id}/chat` | `{question, stream?}` | JSON или **SSE stream** |

`question`: 1–4000 символов. `stream` по умолчанию `false`.

#### ChatResponse (non-stream)

```json
{
  "answer": "...",
  "citations": [
    {
      "file_path": "Contract.sol",
      "function_name": "transfer",
      "start_line": 42,
      "end_line": 58,
      "content": "..."
    }
  ],
  "cached": false,
  "tokens_used": 240,
  "cost_usd": 0.000036
}
```

Поиск: BM25 + MiniLM по листовому контракту из AST (не библиотеки OpenZeppelin). Overview — top findings и затронутые функции. Цитаты только из retrieved `.sol`. Кеш: Redis `llm_cache:v6:{contract_id}:{hash}`; ответы только по сигнатурам интерфейса не кешируются.

#### SSE (`stream=true`)

```
data: {"type": "citations", "citations": [...]}
data: {"type": "token", "content": "The"}
data: {"type": "token", "content": " contract"}
data: {"type": "meta", "cached": false, "tokens_used": 240, "cost_usd": 0.000036}
data: [DONE]
```

#### История чата (`GET`)

Сообщения текущего пользователя по этому контракту, по возрастанию `id`. В LLM не подмешиваются.

```json
[
  {
    "id": 1,
    "role": "user",
    "content": "Кто owner?",
    "citations": [],
    "cached": false,
    "created_at": "2026-09-08T12:00:00"
  },
  {
    "id": 2,
    "role": "assistant",
    "content": "...",
    "citations": [],
    "cached": false,
    "created_at": "2026-09-08T12:00:01"
  }
]
```

---

## Аутентификация

```
Authorization: Bearer <access_token>
```

Либо HttpOnly cookies (`credentials: include` с фронта).

## Коды ошибок

| HTTP | Ситуация |
|---|---|
| 400 | Невалидный адрес, неподдерживаемый `chain_id`, битый upload |
| 401 | Нет / невалидный JWT, неверный логин |
| 403 | Не admin (`require_admin`) или cookie-мутация без `X-Requested-With` |
| 404 | Контракт не найден / нет доступа / исходник не верифицирован на explorer |
| 409 | Email занят / контракт ещё индексируется / чат или PDF до статуса `ready` |
| 429 | Rate limit на `/analyze` или `POST /chat` |
| 503 | Нет `ETHERSCAN_API_KEY` / Google OAuth / сеть explorer / `OPENAI_API_KEY` |

## Supported chain_id

| chain_id | Сеть | Explorer API |
|---|---|---|
| 1 | Ethereum | api.etherscan.io |
| 56 | BSC | api.bscscan.com |
| 11124 | Abstract | api.abscan.org |
