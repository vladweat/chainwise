# Эксплуатация

## Поддерживаемая модель запуска

Репозиторий содержит один Docker Compose stack для локальной разработки и демонстрации:

| Сервис | Зависимости | Постоянные данные |
|---|---|---|
| `postgres` | — | volume `postgres_data` |
| `redis` | — | нет volume |
| `qdrant` | — | volume `qdrant_data` |
| `slither` | mounted solc binaries | нет |
| `backend` | postgres, redis, qdrant, slither | bind mounts исходников |
| `celery-worker` | те же | bind mounts исходников |
| `frontend` | backend start | нет |

Production deployment manifest, TLS termination, ingress, secret manager и orchestrator-конфигурация отсутствуют. Текущий backend запускается с `uvicorn --reload`, а образы Qdrant и часть tooling не закреплены digest/version. Compose нельзя считать production deployment без отдельного hardening.

## Запуск и остановка

```bash
cp .env.example .env
make deps
make up
make health
make logs
make down
```

`make down` не удаляет named volumes. Команды с `docker compose down -v` в проекте нет; удаление volumes уничтожит данные и не является штатной операцией.

## Startup sequence

1. PostgreSQL и Redis проходят Compose healthcheck; Qdrant только запускается.
2. Slither ждёт `/health`.
3. FastAPI lifespan выполняет `alembic upgrade head`. Ошибка миграции останавливает API.
4. Worker импортирует настройки и подключается к broker.
5. Frontend стартует после начала запуска backend, без проверки API health.

Автомиграции удобны для одного экземпляра, но при горизонтальном запуске нескольких API каждый экземпляр попытается применить миграции. Отдельного migration job нет.

## Health и наблюдаемость

`GET /health` всегда возвращает HTTP 200 и тело:

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

При сбое зависимости `status` становится `degraded`, а поле зависимости — `error`; HTTP status при этом не меняется. Endpoint не проверяет Celery worker, broker consumption, embedding model и внешний LLM/explorer.

Логи backend — JSON в stdout. Slither, frontend и базовые образы используют собственный формат. Sentry опционален; Prometheus metrics, tracing и централизованный log collector отсутствуют.

```bash
docker compose ps
docker compose logs backend celery-worker slither
docker compose logs postgres redis qdrant
```

## Миграции

Текущую схему применяет API при старте; вручную:

```bash
make migrate
```

Перед production-миграцией необходим backup PostgreSQL и проверка upgrade на копии данных. Репозиторий содержит downgrade-функции Alembic, но не содержит проверенного rollback runbook. Откат приложения после необратимого изменения данных не гарантирован.

## Backup и recovery

Автоматических backup jobs, retention и restore tests нет. Для полного восстановления нужны как минимум:

- PostgreSQL volume: пользователи, метаданные, исходники, AST, Slither reports, кеш LLM и чат;
- Qdrant volume: воспроизводимые, но дорогие для пересчёта embeddings;
- действующие секреты и конфигурация окружения.

Redis содержит ephemeral locks, rate/quota counters, refresh sessions, Celery broker/results и LLM cache; volume не подключён. После перезапуска Redis refresh-сессии и незабранные задачи могут быть потеряны. Исходники в PostgreSQL позволяют теоретически переиндексировать Qdrant, но массовой команды reindex/recovery в репозитории нет.

Проверенной команды backup/restore документация намеренно не приводит: выбор `pg_dump`, Qdrant snapshots и storage зависит от целевого окружения, которого в репозитории нет. До production необходимо добавить и протестировать отдельный runbook.

## Типовые инциденты

### API показывает `degraded`

Проверить конкретное поле `/health`, затем container logs. Обратите внимание: Qdrant и Slither HTTP checks выполняются с timeout 5 секунд.

### Контракт остаётся в `indexing`

Проверить `celery-worker`, Redis broker и `indexing_step`. Если `celery_task_id` пуст, возможен сбой публикации после DB commit. Автоматического восстановления нет; не исправляйте статус напрямую без проверки Qdrant и lock.

### `failed` на `compile` или `slither`

Смотреть `last_error`, наличие нужного `backend/.solc/solc-vVERSION`, health sidecar и его 90-секундный timeout. Slither report создаётся во временном каталоге и после запроса не сохраняется вне PostgreSQL.

### Чат отвечает `503`

Причины: отсутствует `OPENAI_API_KEY`, ошибка OpenAI-compatible endpoint, timeout 60 секунд, ошибка Qdrant или пустой некорректный ответ. Попадание в PostgreSQL/Redis cache может обслуживаться без LLM.

### Удаление оставило точки Qdrant

Удаление в Qdrant выполняется best effort: ошибка логируется, после чего PostgreSQL row всё равно удаляется. Нужного для автоматической очистки orphan points reconciliation job нет.

## Не реализовано

- zero-downtime deployment и rollback;
- readiness/liveness endpoints с корректными HTTP statuses;
- backup/restore automation и disaster-recovery test;
- метрики очереди, latency, error rate и capacity alerts;
- graceful drain worker и documented task replay;
- data retention и удаление старых LLM reports;
- production sizing и benchmark pipeline.

См. также [установка](./setup.md) и [фоновые задачи](./background-jobs.md).
