# Фоновые задачи и индексация

## Назначение и запуск

Единственная Celery-задача объявлена в `backend/app/celery_config.py`:

```text
indexing.load_contract(contract_id)
```

Worker запускается Compose-командой:

```bash
python -m celery -A app.celery_config worker -l info
```

`CELERY_BROKER_URL` и `CELERY_RESULT_BACKEND` указывают на Redis. Отдельные очереди, routing rules, Celery Beat и периодические задачи не настроены. Сериализация задач и результатов — JSON, timezone — UTC, `task_track_started=True`.

## Жизненный цикл задачи

```mermaid
sequenceDiagram
    participant API as FastAPI
    participant DB as PostgreSQL
    participant R as Redis
    participant C as Celery worker
    participant S as Slither
    participant Q as Qdrant

    API->>R: SET indexing_lock:* NX EX 3600
    API->>DB: INSERT/UPDATE contract (indexing)
    API->>R: publish indexing.load_contract
    API->>DB: save celery_task_id
    C->>DB: load Contract
    C->>DB: compile; commit AST
    C->>S: POST /analyze
    C->>DB: commit Slither report
    C->>Q: replace code chunks
    C->>DB: commit chunk_count
    C->>DB: score; ready; done; commit
    C->>R: DEL indexing_lock:*
```

API-часть реализована в `contract_service._enqueue_indexing`; worker orchestration — в `indexing_worker.run_indexing`.

Шаги `compile`, `slither`, `embed`, `score`, `done` сохраняются в `contracts.indexing_step`. Результаты промежуточных стадий коммитятся отдельно. Это позволяет увидеть последний завершённый шаг, но не обеспечивает атомарность PostgreSQL и Qdrant: ошибка после обновления Qdrant может оставить векторы при `status=failed`.

## Lock и повторные запросы

`backend/app/utils/redis_lock.py` создаёт ключ:

```text
indexing_lock:{user_id}:{chain_id}:{address}
```

Lock имеет TTL 3600 секунд. Повторный analyze:

- возвращает существующий завершённый контракт с `reused=true`;
- отвечает `409`, если статус или Redis lock указывает на текущую индексацию;
- переиспользует failed-запись и запускает pipeline заново.

Это защита от обычных дублей, но не строгая распределённая идемпотентность: значение lock не содержит уникального owner token, а удаление выполняется обычным `DEL`. Если задача работает дольше TTL, другой запуск может получить тот же lock.

## Ошибки, retry и подтверждение

При исключении `load_contract_task`:

1. текущая DB-транзакция откатывается;
2. контракт повторно загружается;
3. выставляются `status=failed` и обрезанный до 2000 символов `last_error`;
4. lock удаляется;
5. исключение пробрасывается в Celery.

Автоматические retry (`autoretry_for`, `self.retry`), retry backoff, `acks_late`, time limits и worker concurrency в приложении явно не заданы. Их фактические значения определяются defaults установленной Celery. Поэтому документированной гарантии повторного выполнения после падения worker нет.

Если публикация задачи после первого DB commit завершится ошибкой, `_enqueue_indexing` удалит lock, но уже сохранённый контракт может остаться в `indexing` без `celery_task_id`. Автоматического reconciler для таких записей нет.

## Операционная диагностика

```bash
docker compose ps
docker compose logs celery-worker
docker compose logs slither
curl -s http://localhost:8001/api/v1/contracts/CONTRACT_ID/status \
  -H "Authorization: Bearer ACCESS_TOKEN"
```

Проверять следует `status`, `indexing_step`, `last_error`, доступность Redis, Qdrant и Slither. `/health` API проверяет зависимости, но не проверяет наличие активного Celery worker и не является readiness probe очереди.

Связанные документы: [архитектура](./architecture.md), [эксплуатация](./operations.md).
