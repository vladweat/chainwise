# База данных

## PostgreSQL 15 (+ pgvector)

Образ: `pgvector/pgvector:pg15`.

Миграции Alembic в `backend/alembic/versions/` применяются при старте сервера (`alembic upgrade head`).

Расширение pgvector предоставляет выбранный образ, но миграции не выполняют `CREATE EXTENSION vector`, а приложение не хранит векторные представления в PostgreSQL. Все векторы находятся в Qdrant.

| Ревизия | Содержание |
|---|---|
| 001 | `users`, `contracts`, `analysis_reports` |
| 002 | `contracts.ast` |
| 003 | `contracts.chunk_count` |
| 004 | `contracts.last_error` |
| 005 | `analysis_reports.citations` + уникальность `(contract_id, question_hash)` |
| 006 | `chat_messages` — история чата пользователя по контракту |
| 007 | `contracts.risk_model`, `features_version` |
| 008 | Принадлежность пользователю: `UNIQUE(user_id, address, chain_id)`, `user_id NOT NULL`, `ON DELETE CASCADE` |
| 009 | `contract_artifacts` (исходники, AST, Slither); `contracts.indexing_step` |
| 010 | UUID попытки индексации, время активности и захвата, индекс зависших записей |
| 011 | `contracts.analysis_generation_id` указывает последнее завершённое поколение анализа |

### Таблица `users`

```sql
CREATE TABLE users (
    id SERIAL PRIMARY KEY,
    email VARCHAR(255) UNIQUE NOT NULL,
    hashed_password VARCHAR(255),
    google_id VARCHAR(255) UNIQUE,
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMP DEFAULT NOW(),
    role VARCHAR(20) DEFAULT 'user'   -- 'user' | 'admin'
);
```

### Таблица `contracts`

```sql
CREATE TABLE contracts (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    address VARCHAR(42) NOT NULL,
    chain_id INTEGER NOT NULL,
    name VARCHAR(255),
    compiler_version VARCHAR(20),
    is_verified BOOLEAN,
    status VARCHAR(20) DEFAULT 'pending',  -- pending | indexing | loaded | ready | failed
    indexing_step VARCHAR(32),             -- compile | slither | embed | score | done
    indexing_attempt_id VARCHAR(36),       -- UUID текущей попытки обработчика
    analysis_generation_id VARCHAR(36),     -- UUID последнего успешного поколения
    indexing_heartbeat_at TIMESTAMPTZ,     -- обновляется между этапами обработки
    indexing_claimed_at TIMESTAMPTZ,       -- атомарный захват одной доставкой
    celery_task_id VARCHAR(255),
    risk_score CHAR(1),                    -- A | B | C | D
    risk_model VARCHAR(20),
    features_version VARCHAR(32),
    chunk_count INTEGER,
    last_error TEXT,
    created_at TIMESTAMP DEFAULT NOW(),
    UNIQUE (user_id, address, chain_id)
);
```

Тяжёлые поля (`source_code`, `ast`, `slither_report`) вынесены в `contract_artifacts` (1:1, CASCADE). ORM-свойства `Contract.source_code` / `.ast` / `.slither_report` обращаются к записи артефакта.

### Таблица `contract_artifacts`

```sql
CREATE TABLE contract_artifacts (
    contract_id INTEGER PRIMARY KEY REFERENCES contracts(id) ON DELETE CASCADE,
    source_code TEXT NOT NULL,
    ast JSONB,
    slither_report JSONB
);
```

### Таблица `analysis_reports`

Кеш ответов LLM. Ключ вопроса включает версию кеша, `analysis_generation_id` и текст
вопроса; уникальность строки — `(contract_id, question_hash)`. Redis использует тот же
хеш в ключе. Поколение публикуется только вместе с переходом контракта в `ready`, поэтому
переиндексация читает отдельные записи кеша Redis и БД и отдельные фрагменты Qdrant. Старые
записи не удаляются для корректности и не смешиваются с новым поколением. При миграции
готовых контрактов PR-06 поколение переносится из `indexing_attempt_id`.

```sql
CREATE TABLE analysis_reports (
    id SERIAL PRIMARY KEY,
    contract_id INTEGER REFERENCES contracts(id) ON DELETE CASCADE,
    user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
    question_hash VARCHAR(64) NOT NULL,    -- SHA-256 от версии кеша, поколения и вопроса
    llm_answer TEXT,
    citations JSONB,                       -- [{file_path, function_name, start_line, end_line, content}]
    tokens_used INTEGER,
    cost_usd DECIMAL(10, 6),
    created_at TIMESTAMP DEFAULT NOW(),
    UNIQUE (contract_id, question_hash)
);
```

Уникальность контрактов отдельно для каждого пользователя по `(user_id, address, chain_id)`. Один адрес в блокчейне у двух пользователей — две независимые копии (и две индексации). Загрузка без адреса в блокчейне использует синтетический `0x` + SHA256.

История диалога в интерфейсе — отдельная таблица `chat_messages`. Кеш не является лентой: один и тот же вопрос может повторяться в чате, ключ кеша уникален.

### Таблица `chat_messages`

Лента чата текущего пользователя по контракту. Не передаётся в LLM.

```sql
CREATE TABLE chat_messages (
    id SERIAL PRIMARY KEY,
    contract_id INTEGER REFERENCES contracts(id) ON DELETE CASCADE,
    user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
    role VARCHAR(20) NOT NULL,             -- 'user' | 'assistant'
    content TEXT NOT NULL,
    citations JSONB,
    cached BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMP DEFAULT NOW()
);
```

### Статус реализации

| Объект | Статус |
|---|---|
| Таблицы + Alembic | ✅ |
| SQLAlchemy модели | ✅ |
| Автомиграция при старте | ✅ |
| Запись `analysis_reports` | ✅ RAG |
| Запись `chat_messages` | ✅ лента чата |
| PDF-отчёт | ✅ генерируется из `contracts` + `slither_report`, отдельной таблицы нет |
| `risk_model` / `features_version` на контракте | ✅ колонки + поля статуса |

`UNIQUE(user_id, address, chain_id)` — идемпотентность загрузки **внутри аккаунта**; повторное использование результата доступно только владельцу записи.

### Индексы и ограничения

- `users.email` — ограничение уникальности и отдельный неуникальный индекс, созданные миграцией 001;
- `users.google_id` — ограничение уникальности;
- `contracts(user_id, address, chain_id)` — ограничение уникальности миграции 008;
- `contracts(status, indexing_heartbeat_at)` — сверка зависшей индексации миграции 010;
- `analysis_reports(contract_id, question_hash)` — ограничение уникальности;
- `analysis_reports.question_hash`, `chat_messages.contract_id`, `chat_messages.user_id` — индексы;
- `contract_artifacts.contract_id` — одновременно PK и FK.

Значения `status`, `role`, `risk_score` и `chat_messages.role` ограничены длиной колонок, но не ограничениями CHECK или перечислениями БД. Допустимые значения контролирует код приложения.

### Сессии и границы транзакций

`core/deps.get_db` создаёт сессию на HTTP-запрос и закрывает её без автоматической фиксации транзакции. Сервисы фиксируют изменения явно. Контракт с UUID попытки и `celery_task_id` сохраняется до публикации сообщения. Ошибка публикации переводит только актуальную попытку в `failed`; при гибели API между этими действиями зависшую запись находит сверка.

Обработчик атомарно захватывает попытку и блокирует строку с проверкой UUID перед каждым изменением. Qdrant не участвует в SQL-транзакции: его точки разделены по поколениям, поиск читает только успешное поколение. См. [фоновые задачи](./background-jobs.md).

---

## Qdrant — коллекция `code_chunks`

Создаётся при первой индексации (`qdrant_indexer`).

| Поле | Тип | Описание |
|---|---|---|
| Вектор | 384 измерения | FastEmbed `all-MiniLM-L6-v2` |
| `contract_id` | int | Ссылка на contracts |
| `file_path` | string | Путь к файлу |
| `function_name` | string | Имя функции/модификатора |
| `start_line` | int | Начало фрагмента |
| `end_line` | int | Конец фрагмента |
| `content` | text | Исходный код |

Точки разделяются по `contract_id` и `indexing_attempt_id`. Новое поколение записывается отдельно; поиск фильтрует `analysis_generation_id` готового контракта. Сверка `reconcile_indexing --apply` удаляет неактивные поколения и точки удалённых контрактов, сохраняя успешное и выполняющееся поколения.

Панель управления: http://localhost:6333/dashboard

---

## Redis

| Шаблон ключа | Назначение |
|---|---|
| `indexing_lock:{user_id}:{chain_id}:{address}` | Идемпотентность индексации отдельно для пользователя (TTL 3600 с) |
| `llm_cache:v7:{contract_id}:{question_hash}` | Кеш ответов LLM (TTL 86400 с) |
| `rl:analyze:{user_id}` / `rl:chat:{user_id}` | Ограничение частоты запросов (окно 60 с) |
| `quota:analyze:{user_id}:{YYYYMMDD}` / `quota:chat:...` | Дневные квоты |
| `refresh:{jti}` | JWT обновления (TTL `REFRESH_TOKEN_EXPIRE_MINUTES`) |

Брокер Celery: `CELERY_BROKER_URL` = Redis.

---

## ER-диаграмма

```mermaid
erDiagram
    users ||--o{ contracts : владеет
    contracts ||--|| contract_artifacts : хранит
    users ||--o{ analysis_reports : запрашивает
    contracts ||--o{ analysis_reports : содержит
    users ||--o{ chat_messages : пишет
    contracts ||--o{ chat_messages : содержит

    users {
        int id PK
        string email UK
        string hashed_password
        string google_id UK
        string role
    }

    contracts {
        int id PK
        int user_id FK
        string address
        int chain_id
        int chunk_count
        string last_error
        char risk_score
    }

    contract_artifacts {
        int contract_id PK,FK
        text source_code
        jsonb ast
        jsonb slither_report
    }

    analysis_reports {
        int id PK
        int contract_id FK
        int user_id FK
        string question_hash
        text llm_answer
        jsonb citations
    }

    chat_messages {
        int id PK
        int contract_id FK
        int user_id FK
        string role
        text content
        jsonb citations
        bool cached
    }
```
