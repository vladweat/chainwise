# Фоновые задачи и индексация

## Назначение и запуск

Единственная Celery-задача объявлена в `backend/app/celery_config.py`:

```text
indexing.load_contract(contract_id)
```

Worker запускается Compose-командой:

```bash
python -m celery -A app.celery_config worker -l info --concurrency=2
```

`CELERY_BROKER_URL` и `CELERY_RESULT_BACKEND` указывают на Redis. Отдельные очереди, routing rules, Celery Beat и периодические задачи не настроены. Сериализация задач и результатов — JSON, timezone — UTC, `task_track_started=True`.

## Пропускная способность analyzer

Analyzer одновременно принимает не более двух compile/Slither запросов. Compose worker ограничен `--concurrency=2`, поэтому обычная локальная очередь не отправляет третью задачу в занятый analyzer. При явном HTTP 503 с `Retry-After` клиенты compile и Slither повторяют запрос с ограниченным ожиданием: до 18 повторов и не более 90 секунд суммарно. Другие 503 (например, отсутствующий solc) не повторяются. Celery autoretry не включён: повторная доставка той же задачи не проходит claim уже захваченной попытки. При запуске нескольких worker-процессов их суммарную concurrency нужно держать в пределах двух на analyzer либо одновременно увеличивать ёмкость analyzer.

## Жизненный цикл задачи и fencing

```mermaid
sequenceDiagram
    participant API as FastAPI
    participant DB as PostgreSQL
    participant R as Redis
    participant C as Celery worker
    participant S as Slither
    participant Q as Qdrant

    API->>R: SET indexing_lock:* <owner-token> NX EX 3600
    API->>DB: save indexing + attempt UUID + heartbeat + task ID
    API->>C: publish task ID + attempt UUID + owner scope/token
    C->>DB: atomic claim (one delivery wins)
    C->>DB: compile; commit AST
    C->>S: POST /analyze
    C->>DB: commit Slither report
    C->>Q: replace this attempt's generation
    C->>DB: commit chunk_count
    C->>DB: lock row, verify attempt UUID, commit ready
    C->>R: EVAL compare owner-token + DEL
```

API-часть реализована в `contract_service._enqueue_indexing`; worker orchestration — в `indexing_worker.run_indexing`.

Перед публикацией API сохраняет `indexing_attempt_id`, `indexing_heartbeat_at` и сгенерированный `celery_task_id` в той же транзакции, что и `status=indexing`. Повторная отправка того же сообщения допускается только одному worker через условный `UPDATE ... WHERE indexing_claimed_at IS NULL`. Дубликаты завершаются без запуска pipeline и не удаляют Redis lock.

Каждая worker-запись блокирует строку `contracts`, проверяет статус и UUID попытки, затем фиксирует изменения. Устаревший worker не может перевести новую попытку в `ready` или `failed`, а также перезаписать её AST/report. Heartbeat обновляется при переходе между шагами. Для старых задач без UUID worker возвращает `skipped`.

## Qdrant поколения

Точки Qdrant получают `indexing_attempt_id` в payload и включают UUID в point ID. Удаление при upsert ограничено парой `(contract_id, indexing_attempt_id)`, а scroll/vector search для READY-контракта фильтруют текущий UUID из PostgreSQL. Redis и `analysis_reports` cache keys тоже включают UUID, поэтому reindex не использует старые ответы/citations. Параллельно завершившаяся старая попытка может оставить только невидимые orphan-точки: она не удалит и не смешает активное поколение. При миграции существующие READY-контракты остаются с UUID `NULL`, поэтому их старые точки и cache keys читаются без generation-фильтра.

PostgreSQL и Qdrant не образуют общую транзакцию. Если процесс погиб после Qdrant upsert, векторы поколения могут сохраниться, а строка останется `indexing`. Они не доступны чату, пока контракт не стал `ready`; reconciler завершает такую строку `failed`, а явный повторный анализ создаёт новый UUID поколения.

## Lock и повторные запросы

`backend/app/utils/redis_lock.py` создаёт ключ:

```text
indexing_lock:{user_id}:{chain_id}:{address}
```

При успешном acquire Redis lock хранит случайный owner token; acquire возвращает token только победившему запросу. Запрос передаёт его Celery вместе с областью lock. Token не включается в HTTP-ответ и скрыт в отображаемом Celery `argsrepr`. API при ошибке и Celery в `finally` используют Lua compare-and-delete: ключ удаляется только если его текущее значение совпадает с token владельца. Старый worker после истечения TTL поэтому не удалит lock, уже захваченный следующей задачей.

Lock имеет TTL 3600 секунд. Повторный analyze:

- возвращает существующий завершённый контракт с `reused=true`;
- отвечает `409`, если статус или Redis lock указывает на текущую индексацию;
- переиспользует failed-запись и запускает pipeline заново.

Это admission lock, а не гарантия согласованности PostgreSQL. Для enqueue API также проверяет `contracts.status`; уникальный индекс `(user_id, address, chain_id)` защищает создание дублирующей записи. Worker использует прежние DB-переходы статуса. Redis lock не заменяет транзакционные проверки или fencing для DB-записей.

Celery `task_time_limit` и `task_soft_time_limit` не заданы, поэтому общего максимального времени задачи нет. Внутренние solc/Slither timeouts и Qdrant client timeout ограничивают отдельные операции; Redis TTL 3600 секунд всё равно может истечь, пока worker ещё выполняется. TTL не является deadline задачи. Owner token не даст старому worker удалить successor-lock, а DB row-lock + attempt UUID блокируют его записи и Qdrant generation не позволит смешать точки. Отдельное продление Redis lease и автоматический retry не нужны для выбранного recovery-механизма.

## Матрица отказов и восстановление

| Отказ | Состояние и риск | Восстановление |
|---|---|---|
| Broker недоступен до publish | API сохраняет попытку, затем условно ставит ей `failed` с redacted `last_error`; lock владельца освобождается. | Повторить analyze после восстановления Redis/broker. API ответ по-прежнему неуспешен при ошибке публикации. |
| API падает после DB commit, но до publish | Строка остаётся `indexing`, `celery_task_id` уже заполнен, claim отсутствует. | Dry-run reconciler обнаружит запись после порога простоя; `--apply` поставит `failed`. Затем пользователь повторяет analyze. |
| Повторная доставка Celery | Только одна доставка может записать `indexing_claimed_at`; остальные возвращают `skipped`. | Не требует вмешательства; проигравшая доставка не освобождает lock победителя. |
| Worker погиб после Qdrant upsert, но до `ready` | Строка остаётся `indexing`; Qdrant содержит поколение попытки, которое не участвует в поиске. | Reconciler помечает stale попытку `failed`; следующий analyze создаёт отдельное поколение. |
| Task timeout или worker потерян | Жёсткий timeout всего Celery task и late ack не включены. Пойманное исключение ставит текущую попытку `failed`; hard kill может оставить `indexing`. | Inner compiler/Slither deadlines остаются; hard kill закрывает соединение и освобождает row lock. Reconciler обрабатывает stale строку. Автоматической Celery redelivery/retry не обещается. |
| Истёк Redis lock | Redis больше не блокирует admission, но DB-статус `indexing` продолжает возвращать API `409`; UUID не даёт старому worker менять новую попытку. | Не удалять/менять статус вручную. Сначала выполнить reconciler после порога простоя; затем повторить analyze. |
| Терминальная ошибка pipeline | Worker условно ставит `failed` и сохраняет ограниченный `last_error`; удаляется только lock победившей попытки. Qdrant generation, если успел записаться, остаётся невидимым. | Исправить причину и повторить analyze; автоматические повторы не настроены. |

Автоматические retry/backoff, `acks_late`, `reject_on_worker_lost` и общие Celery time limits намеренно не включены: безопасная повторная обработка внешних side effects не доказана. Повтор доступен только после terminal `failed`, создаёт новый UUID поколения и ограничен существующей analyze quota. Redis lock остаётся admission lock; DB UUID/status являются fencing для состояния.

## Детерминированная сверка stale строк

Compose stack не запускает Celery Beat. Для небольшого single-host deployment используется явная команда, которую можно запускать по расписанию хоста (например, каждые 15 минут). Порог по умолчанию — 3600 секунд с последнего heartbeat; первый запуск сначала показывает только список ID:

```bash
docker compose exec -T backend python -m app.scripts.reconcile_indexing
docker compose exec -T backend python -m app.scripts.reconcile_indexing --apply
docker compose exec -T backend python -m app.scripts.reconcile_indexing --apply --stale-after-seconds 3600
```

`--apply` блокирует найденные строки, ещё раз проверяет `status=indexing` и завершает их как `failed` с безопасным сообщением. Текущая попытка после этого fenced от worker-записей. Команду можно безопасно запускать повторно; она не публикует задачи и не повторяет pipeline.

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
