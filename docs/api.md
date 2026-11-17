# API

Префикс бизнес-эндпоинтов: `/api/v1`.

Интерактивно: http://localhost:8001/docs · ReDoc: http://localhost:8001/redoc

OpenAPI работающего приложения (`GET /openapi.json`) генерируется FastAPI из маршрутов и схем Pydantic и является источником истины для полных контрактов JSON. Этот документ фиксирует поведение и ограничения; при изменении маршрутов и схем должны обновляться тесты и соответствующий раздел здесь.

## Инфраструктура

| Метод | Путь | Авторизация | Ответ | Описание |
|---|---|---|---|---|
| GET | `/` | — | `{service, docs}` | Информация о сервисе |
| GET | `/health` | — | `HealthResponse` | Проверка состояния |

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

## Аутентификация

| Метод | Путь | Тело | Ответ |
|---|---|---|---|
| POST | `/api/v1/auth/register` | `{email, password}` | `201` `{access_token, token_type}` + cookie |
| POST | `/api/v1/auth/login` | `{email, password}` | `{access_token, token_type}` + cookie |
| POST | `/api/v1/auth/google` | `{id_token}` | `{access_token, token_type}` + cookie |
| POST | `/api/v1/auth/refresh` | cookie `cw_refresh` | новый токен доступа и ротация токена обновления |
| POST | `/api/v1/auth/logout` | cookie + `X-Requested-With` | `204`, cookie сброшены |
| GET | `/api/v1/auth/me` | Bearer JWT **или** cookie `cw_access` | `UserResponse` |
| DELETE | `/api/v1/auth/me` | JWT / cookie | `204`, каскадное удаление пользователя и контрактов, очистка Qdrant |

Пароль при регистрации: 8–128 символов. `token_type` всегда `bearer`. JWT доступа дублируется в cookie с HttpOnly `cw_access`; токен обновления — `cw_refresh` (Redis `refresh:{jti}`). Интерфейс не сохраняет JWT в `localStorage`. Bearer остаётся для Swagger и скриптов.

Мутирующие запросы **только с cookie-сессией** (без `Authorization: Bearer`) требуют заголовок `X-Requested-With` (CSRF). Bearer-запросы и публичные регистрация и вход — без него.

`UserResponse`: `{id, email, role, is_active, created_at}`.

Google OAuth: нужен `GOOGLE_CLIENT_ID`. Без него — `503`. На `/login` кнопка Google Identity Services, если задан `NEXT_PUBLIC_GOOGLE_CLIENT_ID`.

Контракты обычного пользователя **доступны только владельцу** (администратор имеет расширенный доступ): чужой `contract_id` → `404`. Один адрес у двух пользователей — две независимые копии. Квоты (без оплаты): `QUOTA_MAX_CONTRACTS`, `QUOTA_ANALYZE_PER_DAY`, `QUOTA_CHAT_PER_DAY` → `429`.

---

## Контракты

Все методы требуют JWT.

| Метод | Путь | Тело | Ответ |
|---|---|---|---|
| POST | `/api/v1/contracts/analyze` | `{address, chain_id}` | `202` `{task_id, contract_id, status}` |
| POST | `/api/v1/contracts/analyze/upload` | `multipart: file, chain_id?` | `202` `{task_id, contract_id, status}` |
| GET | `/api/v1/contracts` | — | до 50 последних `[ContractStatusResponse]` |
| DELETE | `/api/v1/contracts/{id}` | — | `204`, каскадное удаление контракта, чата и отчётов; очистка Qdrant |
| GET | `/api/v1/contracts/{id}/status` | — | `ContractStatusResponse` (+ `indexing_step`) |
| GET | `/api/v1/contracts/{id}/chat` | — | `[ChatMessage]` |
| POST | `/api/v1/contracts/{id}/chat` | `{question, stream?}` | JSON или **поток SSE** |
| GET | `/api/v1/contracts/{id}/report` | — | `application/pdf` |
| GET | `/api/v1/contracts/{id}/findings` | — | `{contract_id, findings[]}` |
| GET | `/api/v1/contracts/{id}/source` | — | `{contract_id, files: [{path, content}]}` |
| GET | `/api/v1/history` | — | то же, что `GET /contracts` |

### Запуск анализа

- Адрес: `0x` + 40 шестнадцатеричных символов. Сети: `1`, `56`, `11124`.
- Загрузка: `.sol` или `.zip`, до 10 MiB на загруженный файл; полное тело HTTP-запроса multipart ограничено 10 MiB + 256 KiB. Для ZIP также действуют лимиты: до 100 записей, до 10 MiB на Solidity-файл и до 25 MiB исходников после распаковки суммарно. Превышение лимита размера возвращает `413` (раньше ограничение размера возвращало `400`); остальные ошибки формата возвращают `400`. `chain_id` по умолчанию `0`; адрес — SHA256 от `chain_id:source`.
- Повторный запрос при `ready`/`loaded` возвращает существующий контракт без новой задачи.
- Пока идёт индексация — `409`.
- Ограничение частоты запросов в Redis: `429` + `Retry-After` (`RATE_LIMIT_ANALYZE_PER_MINUTE`). Загрузка учитывается тем же лимитом.

### Список и постраничная выдача

`GET /api/v1/contracts` и псевдоним `GET /api/v1/history` не принимают параметров постраничной выдачи, фильтрации или сортировки. Сервис сортирует по `contracts.id DESC` и возвращает максимум 50 записей. Для администратора это последние 50 контрактов всех пользователей; для обычного пользователя — только его записи.

### Отчёт

`GET /api/v1/contracts/{id}/report` — PDF. Контракт `ready` или `loaded`, иначе `409`. В файле: имя, адрес, сеть, дата, оценка A–D, список срабатывания Slither с типом проверки, серьёзностью, уверенностью и расположением. Полный исходник не включается.

### ContractStatusResponse

```json
{
  "contract_id": 1,
  "address": "0x...",
  "chain_id": 1,
  "status": "ready",
  "risk_score": null,
  "risk_model": null,
  "features_version": null,
  "indexing_step": "compile",
  "compiler_version": "0.8.24",
  "name": "MyToken",
  "chunk_count": 12,
  "last_error": null,
  "created_at": "2026-09-08T12:00:00"
}
```

`status`: `pending` | `indexing` | `loaded` | `ready` | `failed`.

`risk_score`: `A` | `B` | `C` | `D` после успешной индексации; `null` пока идёт пайплайн. Это экспериментальный эвристически сформированный вспомогательный индикатор для предварительной проверки, а не вероятность эксплуатации, калиброванная вероятность риска или аудит безопасности. `risk_model`: `xgboost` | `heuristic` указывает фактический источник. `features_version`: сейчас `v1`. Чужой контракт для пользователя без прав администратора — `404`. Подробности: [описание модели Risk Score](./risk-score-model-card.md).

### Найденные проблемы

`GET /api/v1/contracts/{id}/findings` — JWT. Список детекторов Slither: `check`, `impact`, `confidence`, `location`, `file_path`, `start_line`, `end_line`, `description`.

`GET /api/v1/contracts/{id}/source` — JWT. Файлы исходника (`// File: path` → несколько записей) для перехода к найденной проблеме в интерфейсе, не редактор. PDF по-прежнему без исходника.

Фильтры по серьёзности и типу проверки — в интерфейсе.

### Чат

Контракт должен быть `ready`. Иначе `409`. Чужой / нет — `404`. Без `OPENAI_API_KEY` и без попадания в кеш (если есть чанки) — `503`. Слишком частые запросы — `429` (`RATE_LIMIT_CHAT_PER_MINUTE`).

| Метод | Путь | Тело | Ответ |
|---|---|---|---|
| GET | `/api/v1/contracts/{id}/chat` | — | `[ChatMessage]` |
| POST | `/api/v1/contracts/{id}/chat` | `{question, stream?}` | JSON или **поток SSE** |

`question`: 1–4000 символов. `stream` по умолчанию `false`.

#### ChatResponse: ответ без потока

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

Поиск: BM25 + MiniLM по листовому контракту из AST (не библиотеки OpenZeppelin). Обзорный вопрос — основные найденные проблемы и затронутые функции. Цитаты только из найденных `.sol`. Кеш: Redis `llm_cache:v7:{contract_id}:{hash}`; ответы только по сигнатурам интерфейса не кешируются.

#### SSE (`stream=true`)

```
data: {"type": "citations", "citations": [...]}
data: {"type": "token", "content": "Этот"}
data: {"type": "token", "content": " контракт"}
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
    "content": "Кто владелец?",
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

## Передача токена

```
Authorization: Bearer <access_token>
```

Либо cookie с HttpOnly (`credentials: include` из интерфейса).

## Коды ошибок

| HTTP | Ситуация |
|---|---|
| 400 | Невалидный адрес, неподдерживаемый `chain_id`, повреждённый загруженный файл |
| 401 | Нет / невалидный JWT, неверный логин |
| 403 | Нет прав администратора (`require_admin`) или cookie-мутация без `X-Requested-With` |
| 404 | Контракт не найден / нет доступа / исходник не верифицирован в обозревателе блокчейна |
| 409 | Адрес почты занят, контракт индексируется или ещё не готов для запрошенной операции |
| 413 | Загруженный `.sol` или `.zip` превышает лимит 10 MiB либо тело multipart превышает 10 MiB + 256 KiB |
| 429 | Ограничение частоты запросов на `/analyze` или `POST /chat` |
| 503 | Нет `ETHERSCAN_API_KEY` / Google OAuth / сеть обозревателя блокчейна / `OPENAI_API_KEY` |

## Поддерживаемые сети: chain_id

| chain_id | Сеть | API обозревателя блокчейна |
|---|---|---|
| 1 | Ethereum | api.etherscan.io |
| 56 | BSC | api.bscscan.com |
| 11124 | Abstract | api.abscan.org |
