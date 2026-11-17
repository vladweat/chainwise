# Эксплуатация

## Поддерживаемая модель запуска

Docker Compose предназначен для локальной разработки и демонстрации. PostgreSQL хранит данные в томе `postgres_data`, Qdrant — в `qdrant_data`; постоянный том Redis не настроен. API и обработчик используют каталоги исходников хоста.

`docker-compose.production.yml` включает проверки настроек рабочей среды и отключает `uvicorn --reload`. Он наследует монтирование исходников и изменяемые теги образов, а миграции остаются частью запуска API. Перед публичным развёртыванием выполните условия выпуска ниже.

<a id="public-https-entrypoint"></a>

## Сеть и публичный HTTPS-вход

Порты интерфейса 3001, API 8001, PostgreSQL 5432, Redis 6379 и Qdrant 6333 слушают только `127.0.0.1`. API и обработчик находят зависимости по именам Compose. Анализатор подключён к внутренней сети `analyzer`, общей с API и обработчиком; имя сервиса — `slither`. Он не получает адрес БД, учётные данные приложения или доступ к внешней сети.

Анализатор работает под UID 10001, с `no-new-privileges`, `cap_drop: ALL`, корневой файловой системой только для чтения и tmpfs `/tmp` до 256 МиБ. Ограничения: 2 CPU, 2 ГиБ памяти и 64 процесса. Одновременно принимаются два запроса; при заполнении возвращается 503 с `Retry-After`.

Размер HTTP-запроса ограничен 64 МиБ, число файлов — 100, размер файла — 10 МиБ, общий исходный код — 25 МиБ. Вывод solc ограничен 32 МиБ в stdout и 64 КиБ в stderr; Slither — 1 МиБ и 64 КиБ соответственно. JSON-отчёт и HTTP-ответ ограничены 32 МиБ. Процессы solc и Slither ограничены 45 и 90 секундами. Разрешены только заранее установленные версии из `ALLOWED_SOLC_VERSIONS`, доступные в `/opt/solc` только для чтения.

Docker ограничивает права и ресурсы, но не гарантирует защиту от выхода за границы контейнера. `build.network: none` отключает сеть только при сборке; изоляция при работе задаётся отдельно.

Для публичного доступа направьте домен на IP хоста и установите обратный прокси HTTPS на том же хосте. Пример Caddy:

```caddyfile
chainwise.example.com {
    handle /api/* {
        reverse_proxy 127.0.0.1:8001
    }
    handle {
        reverse_proxy 127.0.0.1:3001
    }
}
```

Перед сборкой задайте в `.env`:

```dotenv
ENVIRONMENT=production
SECRET_KEY=<случайный секрет длиной не менее 32 символов>
POSTGRES_PASSWORD=<случайный пароль длиной не менее 32 символов>
ACCESS_TOKEN_EXPIRE_MINUTES=15
COOKIE_SECURE=true
COOKIE_SAMESITE=lax
CORS_ORIGINS=https://chainwise.example.com
NEXT_PUBLIC_API_URL=https://chainwise.example.com
```

При `ENVIRONMENT=production` настройки отвергают слабые секреты и пароли, незащищённые cookie, HTTP и локальные источники CORS, а также срок действия токена доступа вне 5–60 минут. Рекомендуется 15 минут. Локальное значение по умолчанию — 1440 минут. Ошибки называют настройку, но не выводят секрет. Запуск API с `--reload` запрещён.

Для секретов используйте `openssl rand -hex 32`. Такой формат подходит для включения пароля в `DATABASE_URL`. Секреты не публикуйте. Подготовьте зависимости через `make deps`, затем запустите конфигурацию:

```bash
docker compose -f docker-compose.yml -f docker-compose.production.yml config --quiet
docker compose -f docker-compose.yml -f docker-compose.production.yml up -d --build
```

Межсетевой экран должен разрешать публичный вход только на TCP 80/443. Для администрирования БД, Redis и Qdrant используйте VPN или туннель SSH. Проверьте порты:

```bash
docker compose config --quiet
docker compose ps
ss -lntp | grep -E ':(5432|6379|6333|8001|3001)\b'
```

Ожидаются адреса `127.0.0.1`, а не `0.0.0.0` или публичный IP. `config --quiet` проверяет конфигурацию без раскрытия значений окружения.

## Условия выпуска в рабочую среду

1. Закрепить образы неизменяемыми хешами, убрать монтирование исходников и проверить ограничения ресурсов.
2. Организовать хранение секретов, настроить HTTPS, CORS и защищённые cookie. Не публиковать окружение и раскрытый вывод Compose.
3. Проверить межсетевой экран и внешнюю доступность портов; публичным должен быть только обратный прокси.
4. Применять миграции отдельным управляемым процессом. Проверить схему на копии данных и совместимость с версией для отката.
5. Создать согласованные резервные копии PostgreSQL и Qdrant, восстановить их в изолированной среде и проверить поиск.
6. Сохранить перечень компонентов и зависимостей выпуска, проверить их уязвимости и приложить результат проверки. Команда ниже показывает образы, но не сканирует их.
7. Подключить мониторинг, проверить восстановление и назначить ответственного за инциденты.

```bash
docker compose -f docker-compose.yml -f docker-compose.production.yml config --quiet
docker compose -f docker-compose.yml -f docker-compose.production.yml config --images
docker compose ps
```

### Развёртывание и откат

Зафиксируйте идентификатор выпуска, хеши образов, ревизию Alembic, конфигурацию без секретов и проверенную резервную копию. Остановите запись, дождитесь завершения задач, сделайте копии PostgreSQL и Qdrant. Примените миграции и выполните проверки состояния и функций до возврата трафика.

При сбое остановите трафик и обработчик. Если схема совместима, верните прежние образы. Не откатывайте миграции автоматически. При несовместимых данных восстановите согласованную пару хранилищ отдельно, проверьте её и только затем переключите приложение. Автоматизация отката и проверенный откат рабочей установки отсутствуют.

## Запуск и остановка

```bash
cp .env.example .env
make deps
make up
make health
make logs
make down
```

`make down` сохраняет именованные тома. Удаление томов уничтожает данные.

## Порядок запуска

PostgreSQL и Redis проходят проверки состояния; Qdrant должен быть запущен, анализатор — отвечать на `/health`. API применяет `alembic upgrade head`; ошибка миграции останавливает запуск. Обработчик подключается к брокеру, интерфейс запускается после начала запуска API. Несколько экземпляров API могут одновременно пытаться применить миграции, поэтому для рабочего развёртывания нужен отдельный этап миграций.

## Проверка состояния и наблюдаемость

`GET /health` возвращает HTTP 200:

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

При отказе зависимости её поле становится `error`, общий статус — `degraded`. Проверяйте тело JSON, а не только HTTP-код. Этот маршрут не проверяет наличие обработчика Celery, обработку очереди, модель векторизации или внешний сервис языковой модели и обозреватель блокчейна.

```bash
curl -fsS http://127.0.0.1:8001/health | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d); sys.exit(d.get("status") != "ok")'
docker compose ps
docker compose logs --since=15m backend celery-worker slither postgres redis qdrant
```

Сервер выводит журналы JSON в stdout. Остальные сервисы используют собственный формат. Sentry подключается отдельно; централизованный сбор журналов, метрик и трассировок не настроен.

Условия для внешних оповещений: ухудшение `/health`, остановка обработчика, рост очереди или ошибок индексации, отсутствие активности задачи более 3600 секунд, нехватка места или индексных дескрипторов файлов, устаревшие резервные копии, ошибки контрольных сумм и восстановления, истечение сертификата HTTPS.

## Миграции

```bash
make migrate
```

Перед изменением рабочей схемы сделайте резервную копию PostgreSQL и проверьте применение миграций на копии данных.

## Резервное копирование и восстановление

Автоматические копии, сроки хранения и проверка аварийного восстановления не настроены. PostgreSQL — основное долговечное состояние. Qdrant содержит производные данные, но до проверки полного пересоздания индекса его снимок также обязателен. Кеш ответов Redis можно пересоздать; потеря очереди может потерять задачи, а потеря сеансов потребует повторного входа. После отказа Redis выполните сверку зависших попыток.

### Создать согласованную резервную копию

Закройте входящую запись, дождитесь нулевого числа активных индексаций и только после этого остановите API, интерфейс и обработчик. PostgreSQL и Qdrant оставьте включёнными. Если счётчик не равен нулю, сначала расследуйте зависшие задачи.

```bash
docker compose exec -T postgres sh -ec 'PGPASSWORD="$POSTGRES_PASSWORD" psql -h 127.0.0.1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Atc "SELECT count(*) FROM contracts WHERE status = '\''indexing'\''"'
docker compose stop frontend backend celery-worker
```

Храните `.env` отдельно как секрет. Копии зашифруйте и вынесите с хоста. Не публикуйте архивы, исходники и секреты в журналах.

```bash
umask 077
export BACKUP_DIR="$PWD/backups/$(date -u +%Y%m%dT%H%M%SZ)"
mkdir -p "$BACKUP_DIR"

# Логический архив PostgreSQL (понадобится pg_restore).
docker compose exec -T postgres sh -ec 'PGPASSWORD="$POSTGRES_PASSWORD" pg_dump -h 127.0.0.1 -U "$POSTGRES_USER" -Fc "$POSTGRES_DB"' > "$BACKUP_DIR/postgres.dump"
docker compose exec -T -i postgres sh -ec 'pg_restore --list' < "$BACKUP_DIR/postgres.dump" > "$BACKUP_DIR/postgres.toc"
docker compose exec -T postgres sh -ec 'PGPASSWORD="$POSTGRES_PASSWORD" psql -h 127.0.0.1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Atc "SELECT (SELECT count(*) FROM users), (SELECT count(*) FROM contracts), (SELECT count(*) FROM contract_artifacts), (SELECT count(*) FROM analysis_reports), (SELECT count(*) FROM chat_messages)"' > "$BACKUP_DIR/postgres-row-counts.txt"
docker compose exec -T postgres sh -ec 'PGPASSWORD="$POSTGRES_PASSWORD" psql -h 127.0.0.1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Atc "SELECT version_num FROM alembic_version"' > "$BACKUP_DIR/alembic-version.txt"

# Перед снимком убедитесь, что коллекция code_chunks существует.
curl -fsS -X POST http://127.0.0.1:6333/collections/code_chunks/snapshots
# Из result.name в JSON выше задайте имя снимка:
export SNAPSHOT='<snapshot-name-from-result>'
curl -fS "http://127.0.0.1:6333/collections/code_chunks/snapshots/$SNAPSHOT" -o "$BACKUP_DIR/qdrant.snapshot"
curl -fsS http://127.0.0.1:6333/collections/code_chunks > "$BACKUP_DIR/qdrant-collection-info.json"

sha256sum "$BACKUP_DIR/postgres.dump" "$BACKUP_DIR/qdrant.snapshot" > "$BACKUP_DIR/SHA256SUMS"
```

`pg_dump -Fc` создаёт архив, проверяемый через `pg_restore --list`. Описание команд: [pg_dump](https://www.postgresql.org/docs/15/app-pgdump.html), [pg_restore](https://www.postgresql.org/docs/15/app-pgrestore.html). Снимок коллекции Qdrant включает настройки и точки; требования совместимости версий сверяйте с [документацией Qdrant](https://qdrant.tech/documentation/snapshots/). Доступ к Qdrant должен оставаться административным, через частную сеть или VPN.

### Проверить восстановление перед рабочим развёртыванием

Используйте отдельные PostgreSQL и Qdrant с совместимыми версиями. Не подменяйте тестовые адреса рабочими.

```bash
set -euo pipefail

# Проверить файлы перед восстановлением из каталога резервной копии.
cd "$BACKUP_DIR" && sha256sum -c SHA256SUMS

# PostgreSQL: отдельная пустая тестовая база.
docker compose exec -T postgres sh -ec 'PGPASSWORD="$POSTGRES_PASSWORD" createdb -h 127.0.0.1 -U "$POSTGRES_USER" chainwise_restore_check'
docker compose exec -T -i postgres sh -ec 'PGPASSWORD="$POSTGRES_PASSWORD" pg_restore --list' < "$BACKUP_DIR/postgres.dump" > /dev/null
docker compose exec -T -i postgres sh -ec 'PGPASSWORD="$POSTGRES_PASSWORD" pg_restore --exit-on-error --no-owner --no-privileges -h 127.0.0.1 -U "$POSTGRES_USER" -d chainwise_restore_check' < "$BACKUP_DIR/postgres.dump"

# Поднять отдельный одноразовый Qdrant без подключённого тома.
# Укажите совместимую точную версию или хеш образа; не используйте изменяемый тег latest.
: "${STAGING_QDRANT_IMAGE:?Укажите закреплённый совместимый образ Qdrant в STAGING_QDRANT_IMAGE}"
docker run --rm -d --name chainwise-qdrant-restore-check \
  -p 127.0.0.1:6335:6333 "$STAGING_QDRANT_IMAGE"
trap 'docker stop chainwise-qdrant-restore-check >/dev/null 2>&1 || true' EXIT
STAGING_QDRANT_URL=http://127.0.0.1:6335
for attempt in {1..30}; do
  if curl -fsS "$STAGING_QDRANT_URL/healthz" >/dev/null; then break; fi
  sleep 1
done
curl -fsS "$STAGING_QDRANT_URL/healthz" >/dev/null

# Убедиться, что цель новая и пустая; не заменяйте URL на рабочий Qdrant.
curl -fsS "$STAGING_QDRANT_URL/collections" | python3 -c 'import json,sys; collections=json.load(sys.stdin)["result"]["collections"]; assert not collections, "Тестовый Qdrant не пуст"'
curl -fS -X POST "$STAGING_QDRANT_URL/collections/code_chunks/snapshots/upload?priority=snapshot" -F "snapshot=@$BACKUP_DIR/qdrant.snapshot"
curl -fsS "$STAGING_QDRANT_URL/collections/code_chunks"

# При выходе остановится только созданный временный контейнер.
```

Сверьте SHA-256, ревизию Alembic, число строк из сохранённых файлов и число точек Qdrant. Проверьте размерность векторов и точки успешного поколения. Подключите отдельный тестовый API к восстановленной паре хранилищ и проверьте поиск по `analysis_generation_id`, цитаты и вопрос по синтетическому контракту. Описание [загрузки снимка Qdrant](https://api.qdrant.tech/api-reference/snapshots/recover-from-uploaded-snapshot).

На 2026-11-06 команды были проверены на временной базе PostgreSQL и временных коллекциях Qdrant 1.20.0: контрольная строка восстановилась, количество точек совпало. Временные объекты удалены. Это не подтверждает целевые сроки потери данных и восстановления рабочей установки. После копирования запустите сервисы, проверьте состояние и верните трафик.

## Типовые инциденты

### API показывает `degraded`

Проверьте поле отказавшей зависимости и журналы. HTTP-проверки Qdrant и Slither ограничены пятью секундами.

### Контракт остаётся в `indexing`

Проверьте обработчик, брокер, `celery_task_id`, `indexing_step` и `indexing_heartbeat_at`. Сначала выполните сверку без изменений, затем примените результат:

```bash
docker compose exec -T backend python -m app.scripts.reconcile_indexing
docker compose exec -T backend python -m app.scripts.reconcile_indexing --apply
```

Порог простоя — 3600 секунд. Для автоматизации установите [таймер systemd](./background-jobs.md#детерминированная-сверка-зависших-записей). После `failed` пользователь может повторить анализ. Не меняйте статус вручную.

### `failed` на `compile` или `slither`

Проверьте `last_error`, наличие нужного `backend/.solc/solc-vVERSION` и состояние анализатора. Отчёт Slither создаётся во временном каталоге и сохраняется в PostgreSQL.

### Чат отвечает `503`

Проверьте ключ `OPENAI_API_KEY`, адрес сервиса языковой модели, предельное время запроса 60 секунд, ответ модели и доступность Qdrant. Готовый кешированный ответ может возвращаться без вызова модели.

### Удаление оставило точки Qdrant

Удаление сначала фиксируется в PostgreSQL. Ошибка последующего удаления из Qdrant записывается в журнал. Следующая сверка `reconcile_indexing --apply` удаляет осиротевшие точки и неактивные поколения, сохраняя успешное и выполняющееся поколения.

## Не реализовано

Развёртывание без перерыва, автоматический откат, автоматические резервные копии, метрики очереди и задержек, управляемое завершение обработчиков, автоматическое повторное выполнение потерянных задач и сроки хранения старых ответов требуют отдельной настройки и проверки.

См. [установку](./setup.md) и [фоновые задачи](./background-jobs.md).
