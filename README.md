# ChainWise

ChainWise — веб-приложение для предварительного анализа Solidity-контрактов. Оно загружает верифицированный исходник по адресу либо принимает `.sol`/`.zip`, запускает solc и Slither, вычисляет ориентировочный Risk Score A–D и отвечает на вопросы по коду с цитатами.

> ChainWise не заменяет профессиональный аудит. Risk Score обучен на синтетических данных, а ответы LLM ограничены найденным контекстом.

## Реализованные возможности

- регистрация и вход по email/password или Google ID token;
- приватные контракты и история чата на пользователя;
- загрузка из Ethereum, BSC и Abstract explorer либо из файла;
- асинхронный pipeline `solc → Slither → FastEmbed → Qdrant → Risk Score`;
- RAG-чат через OpenAI-совместимый API, JSON и SSE, цитаты по строкам;
- findings, просмотр исходника и PDF-отчёт;
- Redis rate limits, дневные квоты, кеш ответов и lock индексации.

## Стек

Python 3.12, FastAPI 0.115, SQLAlchemy 2, Alembic, Celery 5, PostgreSQL 15, Redis 7, Qdrant, FastEmbed MiniLM, Slither, Next.js 15, React 19 и TypeScript 5. Точные ограничения версий находятся в `backend/requirements.txt` и `frontend/package.json`.

## Требования

- Docker с Compose;
- Python 3.12 и Node.js 20+ на Linux x86_64 для подготовки offline-зависимостей;
- доступ в сеть при первом `make deps`;
- `OPENAI_API_KEY` для чата и `ETHERSCAN_API_KEY` для загрузки по адресу.

## Быстрый старт

```bash
cp .env.example .env
# Замените SECRET_KEY и POSTGRES_PASSWORD; API-ключи не коммитьте.
make deps
make up
make health
```

| Компонент | URL |
|---|---|
| Frontend | <http://localhost:3001> |
| Swagger UI | <http://localhost:8001/docs> |
| Qdrant Dashboard | <http://localhost:6333/dashboard> |

`make deps` скачивает Python-, Slither-, solc-, embedding- и frontend-зависимости на хост. Dockerfile собираются с `network: none` и копируют эти артефакты. Поэтому перенос подготовленных каталогов между другой ОС или архитектурой не поддержан.

## Разработка и проверки

```bash
make test          # pytest в уже запущенном backend-контейнере
make lint          # flake8 в backend-контейнере
cd frontend && npx tsc --noEmit
make logs
make down
```

`make test` и `make lint` требуют запущенного Compose-стека (`make up`). CI выполняет flake8, TypeScript typecheck и pytest; frontend unit/E2E tests в репозитории отсутствуют.

## Конфигурация

Настройки backend загружаются из окружения через `backend/app/core/config.py`. Начальная форма — `.env.example`; секретные значения должны оставаться в локальном `.env`. `NEXT_PUBLIC_*` встраиваются во frontend во время сборки. Полный справочник: [docs/environment.md](./docs/environment.md).

## Документация

- [Навигация по документации](./docs/README.md)
- [Архитектура](./docs/architecture.md)
- [Установка и разработка](./docs/setup.md)
- [Backend](./docs/backend.md)
- [Фоновые задачи](./docs/background-jobs.md)
- [API](./docs/api.md)
- [Эксплуатация](./docs/operations.md)
- [Безопасность](./docs/security.md)

## Известные ограничения запуска

- Compose рассчитан на разработку: backend запущен с `--reload`, TLS/reverse proxy не настроены.
- Миграции выполняются автоматически при старте API; отдельного production-процесса деплоя и rollback в репозитории нет.
- Backup/restore, мониторинг метрик и production-оркестрация не автоматизированы.
