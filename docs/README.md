# ChainWise — документация

Веб-приложение для предварительного анализа Solidity-контрактов.

## Навигация

| Документ | Описание |
|---|---|
| [Обзор проекта](./overview.md) | Назначение, возможности и ограничения |
| [Архитектура](./architecture.md) | Стек, схема сервисов, потоки данных |
| [Фоновые задачи](./background-jobs.md) | Celery lifecycle, lock, ошибки и диагностика |
| [Установка и запуск](./setup.md) | Offline-сборка, Docker, локальный dev |
| [Backend](./backend.md) | Структура API-сервера, пайплайн индексации |
| [Frontend](./frontend.md) | Next.js, компоненты, API-клиент |
| [API](./api.md) | HTTP-эндпоинты и их поведение |
| [База данных](./database.md) | PostgreSQL, Qdrant, Redis |
| [Переменные окружения](./environment.md) | `.env`, секреты, конфигурация |
| [Эксплуатация](./operations.md) | Startup, health, миграции, recovery и инциденты |
| [Безопасность](./security.md) | Auth, CSRF/CORS, доверенные границы и ограничения |

## Быстрый старт

```bash
cp .env.example .env
make deps          # с интернетом
make up            # offline
```

- Frontend: http://localhost:3001
- API Swagger: http://localhost:8001/docs
- Qdrant Dashboard: http://localhost:6333/dashboard

## Структура репозитория

```
chainwise/
├── docs/                 # Документация проекта
├── backend/              # FastAPI + Celery
├── frontend/             # Next.js 15
├── slither/              # Sidecar: slither --json
├── ml_models/            # risk_xgb_model.pkl
├── .github/workflows/ci.yml
├── docker-compose.yml
├── Makefile
├── .env.example
├── CONTRIBUTING.md        # Процесс внесения изменений
└── README.md              # Быстрый старт и ограничения
```
