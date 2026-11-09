# Эксплуатация

## Поддерживаемая модель запуска

Репозиторий содержит один Docker Compose stack для локальной разработки и демонстрации:

| Сервис | Зависимости | Постоянные данные |
|---|---|---|
| `postgres` | — | volume `postgres_data` |
| `redis` | — | нет volume |
| `qdrant` | — | volume `qdrant_data` |
| `slither` | analyzer: preinstalled solc + Slither | нет |
| `backend` | postgres, redis, qdrant, slither | bind mounts исходников |
| `celery-worker` | те же | bind mounts исходников |
| `frontend` | backend start | нет |

Compose остаётся стеком разработки по умолчанию. Файл `docker-compose.production.yml` задаёт production-only settings и заменяет `uvicorn --reload`, но не является полноценным production manifest: secret manager и orchestrator-конфигурация отсутствуют, а образы Qdrant и часть tooling не закреплены digest/version. Привязка портов к loopback защищает host-порты от прямого внешнего доступа, но сама по себе не делает stack production deployment.

<a id="public-https-entrypoint"></a>

## Сеть и публичный HTTPS-вход

Опубликованные порты Compose слушают только `127.0.0.1`: frontend `3001`, backend `8001`, PostgreSQL `5432`, Redis `6379` и Qdrant `6333`. Это сохраняет локальные URL и host-native workflow, но не открывает их для других машин. Backend и worker по-прежнему находят PostgreSQL, Redis и Qdrant по DNS-именам `postgres`, `redis` и `qdrant` в стандартной Compose-сети.

Analyzer подключён только к отдельной Compose-сети `analyzer` с `internal: true`; backend и worker подключены одновременно к ней и к стандартной сети. Оба процесса обращаются к нему по имени `slither`. Sidecar не подключён к сети с PostgreSQL, Redis, Qdrant и frontend, не получает DB URL или прикладных credentials, а `analyzer` не даёт ему внешнего сетевого доступа. Runtime-профиль использует UID `10001`, `no-new-privileges`, `cap_drop: ALL`, read-only root, `/tmp` tmpfs до 256 MiB и лимиты 2 CPU, 2 GiB RAM и 64 PID. Он принимает максимум два параллельных analyzer-запроса; при заполнении отвечает `503` с `Retry-After`. Запрос ограничен 64 MiB, вход — 100 файлов, 10 MiB на файл и 25 MiB суммарно; solc stdout — 32 MiB и stderr — 64 KiB; Slither stdout — 1 MiB, stderr — 64 KiB, JSON report и HTTP JSON — 32 MiB. Solc subprocess ограничен 45 секундами, Slither — 90 секундами. Разрешены только версии из `ALLOWED_SOLC_VERSIONS`, заранее установленные в read-only `/opt/solc`; новые бинарники не скачиваются при обработке запроса. Эти Docker controls ограничивают права и ресурсы, но не гарантируют защиту от container escape или уязвимости ядра/runtime. `build.network: none` ограничивает только сборку, отдельно от runtime-сети `analyzer`.

Для публичного доступа укажите домен на публичный IP хоста и используйте HTTPS reverse proxy вне Compose, работающий на этом же хосте и принимающий трафик только на 80/443. Например, Caddy, установленный как host service, автоматически получает TLS-сертификат для домена и проксирует браузерный API и frontend:

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

Перед сборкой frontend задайте в локальном `.env`:

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

Production Settings проверяются до запуска API и worker. Проверка отвергает placeholder/слабые `SECRET_KEY` и пароль в `DATABASE_URL`, незащищённые cookies, localhost или HTTP origins, а также access-token lifetime вне диапазона 5–60 минут. Ошибки называют настройку и способ исправления, но не выводят её значение. `--reload` блокируется при старте API в production.

Access token действует от 5 до 60 минут; рекомендованное значение — 15. Development default `1440` минут и поведение существующих локальных тестов сохранены; production overlay требует явного значения, а `REFRESH_TOKEN_EXPIRE_MINUTES` не меняется.

Секреты не коммитьте. Для `SECRET_KEY` и `POSTGRES_PASSWORD` используйте, например, `openssl rand -hex 32`; hex-формат безопасно вставляется в текущий `DATABASE_URL`. Перед сборкой подготовьте локальные build dependencies командой `make deps`, затем запустите overlay:

```bash
docker compose -f docker-compose.yml -f docker-compose.production.yml config --quiet
docker compose -f docker-compose.yml -f docker-compose.production.yml up -d --build
```

В firewall хоста разрешите публичный вход только на TCP 80/443; не публикуйте PostgreSQL, Redis или Qdrant на публичном интерфейсе. Для администрирования data services используйте VPN или SSH tunnel. Этот reverse-proxy пример задаёт безопасную сетевую границу, но не заменяет отдельную production-конфигурацию, отключение `--reload`, фиксацию image versions и проверку секретов.

После запуска проверьте итоговую конфигурацию, опубликованные порты и сокеты хоста:

```bash
docker compose config --quiet
docker compose ps
ss -lntp | grep -E ':(5432|6379|6333|8001|3001)\b'
```

`docker compose config --quiet` проверяет конфигурацию без вывода раскрытых переменных окружения. В `docker compose ps` ожидаются только адреса вида `127.0.0.1:<порт>`. В `ss` сервисные порты должны слушать на `127.0.0.1`, не на `0.0.0.0` или публичном IP. Команды предполагают, что stack запущен.

## Production release gate

### Текущий статус: deployment заблокирован до выполнения gate

`docker-compose.production.yml` включает production Settings и убирает `uvicorn --reload`, но наследует bind mounts исходников, использует mutable image tags (`qdrant/qdrant:latest`, `chainwise-backend:latest`) и оставляет миграцию БД на startup каждого API. Это overlay для локальной проверки production-настроек, не готовая production-конфигурация. В репозитории нет secret manager, отдельного migration job, rollback automation, автоматических backups/restore drill или внешнего мониторинга.

Открывайте публичный доступ только после выполнения всех пунктов:

1. Развернуть release images по неизменяемым digest; убрать source bind mounts и задавать проверенные runtime limits для всех сервисов.
2. Хранить production credentials в secret manager; установить production origins, secure cookies и сильные секреты. Не печатать переменные окружения и `docker compose config` без `--quiet` в публичных CI logs.
3. Публиковать наружу только HTTPS reverse proxy на TCP 443 (и 80 только для redirect/ACME). PostgreSQL, Redis, Qdrant, analyzer и внутренний backend port должны быть доступны только приватной сети/административному VPN; проверить host firewall и внешний port scan.
4. Применить миграцию контролируемым одноразовым процессом на staging-копии и подтвердить совместимость схемы с версией для rollback. Текущая автоматическая миграция при API startup для этого gate не подходит.
5. Создать согласованную пару PostgreSQL backup + Qdrant snapshot, восстановить её в изолированной staging-среде и подтвердить данные и RAG-поиск до rollout.
6. До релиза зафиксировать inventory/SBOM, выполнить dependency/image vulnerability review выбранным организацией scanner и сохранить отчёт к release. `docker compose ... config --images` ниже показывает только объявленные image references; это **не** security scan. CI проекта таких audit jobs не выполняет.
7. Подключить внешние health/alert checks из раздела [Health и наблюдаемость](#health-и-наблюдаемость), проверить восстановление по этому runbook и назначить владельца on-call.

Минимальный локальный preflight Compose:

```bash
docker compose -f docker-compose.yml -f docker-compose.production.yml config --quiet
docker compose -f docker-compose.yml -f docker-compose.production.yml config --images
docker compose ps
```

Сверяйте список и digest каждого release image с одобренным release inventory. Ни вывод `config --images`, ни зелёный `/health` не подтверждают production readiness.

### Rollout и rollback

1. Записать release ID, immutable image digests, текущую Alembic revision, конфигурацию без секретов и проверенную точку backup.
2. Остановить входящую запись, дождаться завершения индексации и выполнить backup PostgreSQL и Qdrant. Проверить backup restore на изолированной staging-паре.
3. Применить миграцию отдельным контролируемым job, если она требуется; развернуть release и выполнить health/functional checks до возврата трафика.
4. При сбое снова остановить трафик и worker. Если схема и данные совместимы со старой версией, вернуть последний проверенный image digest. Не запускать Alembic downgrade автоматически.
5. Если откат приложения несовместим с изменёнными данными, восстановить согласованную пару PostgreSQL + Qdrant в изолированную среду, проверить её, затем переключить приложение. Не восстанавливать snapshot поверх live stores до проверки.

Этот порядок — операционный gate, а не существующий deployment/rollback script; production rollback в проекте не запускался и не проверялся.

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

Проверяйте JSON body, а не только HTTP status:

```bash
curl -fsS http://127.0.0.1:8001/health | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d); sys.exit(d.get("status") != "ok")'
docker compose ps
docker compose logs --since=15m backend celery-worker slither postgres redis qdrant
```

Логи backend — JSON в stdout. Slither, frontend и базовые образы используют собственный формат. Sentry опционален; Prometheus metrics, tracing и централизованный log collector отсутствуют.

Самостоятельно подключите внешний мониторинг. Практические alert conditions для оператора:

- `/health` body `status != ok` или любая зависимость имеет `error`;
- `celery-worker` не работает, очередь растёт, или контракт остаётся `indexing` без heartbeat более 3600 секунд;
- растёт число `failed` индексаций или повторяются analyzer/Qdrant ошибки;
- заканчивается свободное место/inodes для PostgreSQL или Qdrant volumes;
- backup старше заданного RPO, snapshot/dump checksum не сходится или staging restore не проходит;
- HTTPS certificate скоро истечёт или reverse proxy не может обновить сертификат.

Это рекомендуемые условия внешних checks; в проекте нет готовой системы мониторинга, метрик очереди или alert rules.

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

Перед production-миграцией необходим backup PostgreSQL и проверка upgrade на копии данных. Хотя этот документ задаёт rollback-порядок, он не автоматизирован и не проверен в production. Откат приложения после несовместимого изменения данных не гарантирован.

## Backup и recovery

Автоматических backup jobs, retention и production disaster-recovery tests нет. Ниже — ручная процедура для operator-controlled staging; это не настроенный backup service.

PostgreSQL — авторитетное состояние: users, contracts, исходники/AST/Slither, отчёты и история чата. Qdrant `code_chunks` содержит производные embeddings, но текущий сервису нужен для RAG, а полной автоматической переиндексации нет; до отдельно проверенного rebuild его нужно считать обязательной частью восстановления. Redis не хранится в volume. Его LLM response cache можно пересоздать; потеря брокера может потерять queued tasks, а refresh sessions, locks и rate/quota counters — временные. После Redis loss пользователям может понадобиться войти снова; запустите reconciliation и повторно отправьте завершившиеся `FAILED` анализы. [Таблица состояния хранилищ](./architecture.md#классы-состояния-и-восстановление).

### Создать согласованную точку backup

Для PostgreSQL и Qdrant используйте одну остановленную по записи точку: закройте write traffic, дождитесь завершения задач и проверьте число активных индексаций; только при `0` остановите API, frontend и worker, оставив PostgreSQL/Qdrant включёнными. Храните файл `.env` отдельно как секрет, а backup-файлы зашифруйте и вынесите с host. Не отправляйте archive, SQL, secret values или исходники в публичные logs.

```bash
docker compose exec -T postgres sh -ec 'PGPASSWORD="$POSTGRES_PASSWORD" psql -h 127.0.0.1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Atc "SELECT count(*) FROM contracts WHERE status = '\''indexing'\''"'
docker compose stop frontend backend celery-worker
```

Если count не равен нулю, не останавливайте worker до завершения задач; расследуйте застрявшие строки через [reconciliation runbook](./background-jobs.md#матрица-отказов-и-восстановление). После снятия backup сначала перезапустите сервисы и проверьте `/health`, затем верните трафик.

```bash
umask 077
export BACKUP_DIR="$PWD/backups/$(date -u +%Y%m%dT%H%M%SZ)"
mkdir -p "$BACKUP_DIR"

# Custom-format logical dump PostgreSQL (понадобится pg_restore).
docker compose exec -T postgres sh -ec 'PGPASSWORD="$POSTGRES_PASSWORD" pg_dump -h 127.0.0.1 -U "$POSTGRES_USER" -Fc "$POSTGRES_DB"' > "$BACKUP_DIR/postgres.dump"
docker compose exec -T -i postgres sh -ec 'pg_restore --list' < "$BACKUP_DIR/postgres.dump" > "$BACKUP_DIR/postgres.toc"
docker compose exec -T postgres sh -ec 'PGPASSWORD="$POSTGRES_PASSWORD" psql -h 127.0.0.1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Atc "SELECT (SELECT count(*) FROM users), (SELECT count(*) FROM contracts), (SELECT count(*) FROM contract_artifacts), (SELECT count(*) FROM analysis_reports), (SELECT count(*) FROM chat_messages)"' > "$BACKUP_DIR/postgres-row-counts.txt"
docker compose exec -T postgres sh -ec 'PGPASSWORD="$POSTGRES_PASSWORD" psql -h 127.0.0.1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Atc "SELECT version_num FROM alembic_version"' > "$BACKUP_DIR/alembic-version.txt"

# Перед snapshot убедитесь, что collection code_chunks существует.
curl -fsS -X POST http://127.0.0.1:6333/collections/code_chunks/snapshots
# Из result.name в JSON выше задайте имя snapshot:
export SNAPSHOT='<snapshot-name-from-result>'
curl -fS "http://127.0.0.1:6333/collections/code_chunks/snapshots/$SNAPSHOT" -o "$BACKUP_DIR/qdrant.snapshot"
curl -fsS http://127.0.0.1:6333/collections/code_chunks > "$BACKUP_DIR/qdrant-collection-info.json"

sha256sum "$BACKUP_DIR/postgres.dump" "$BACKUP_DIR/qdrant.snapshot" > "$BACKUP_DIR/SHA256SUMS"
```

`pg_dump -Fc` формирует custom archive, который проверяется `pg_restore --list` ([PostgreSQL 15 pg_dump](https://www.postgresql.org/docs/15/app-pgdump.html), [pg_restore](https://www.postgresql.org/docs/15/app-pgrestore.html)). Snapshot Qdrant collection включает её конфигурацию и points; при восстановлении используйте ту же или следующую minor-версию Qdrant и сверяйте ограничения версии в [официальной документации snapshot](https://qdrant.tech/documentation/snapshots/). В production API Qdrant должен быть доступен только администратору через private network/VPN, не через публичный proxy.

### Проверить восстановление до production

Восстанавливайте только в изолированные staging PostgreSQL и Qdrant, с совместимыми версиями. Не используйте production DB/collection в качестве тестовой цели.

```bash
set -euo pipefail

# Проверить файлы перед restore (выполнять из каталога backup).
cd "$BACKUP_DIR" && sha256sum -c SHA256SUMS

# PostgreSQL: отдельная пустая staging database.
docker compose exec -T postgres sh -ec 'PGPASSWORD="$POSTGRES_PASSWORD" createdb -h 127.0.0.1 -U "$POSTGRES_USER" chainwise_restore_check'
docker compose exec -T -i postgres sh -ec 'PGPASSWORD="$POSTGRES_PASSWORD" pg_restore --list' < "$BACKUP_DIR/postgres.dump" > /dev/null
docker compose exec -T -i postgres sh -ec 'PGPASSWORD="$POSTGRES_PASSWORD" pg_restore --exit-on-error --no-owner --no-privileges -h 127.0.0.1 -U "$POSTGRES_USER" -d chainwise_restore_check' < "$BACKUP_DIR/postgres.dump"

# Поднять отдельный одноразовый Qdrant без подключённого volume.
# Укажите точную версию или digest того же либо следующего совместимого minor; не используйте mutable `latest`.
: "${STAGING_QDRANT_IMAGE:?Set STAGING_QDRANT_IMAGE to a pinned compatible Qdrant image}"
docker run --rm -d --name chainwise-qdrant-restore-check \
  -p 127.0.0.1:6335:6333 "$STAGING_QDRANT_IMAGE"
trap 'docker stop chainwise-qdrant-restore-check >/dev/null 2>&1 || true' EXIT
STAGING_QDRANT_URL=http://127.0.0.1:6335
for attempt in {1..30}; do
  if curl -fsS "$STAGING_QDRANT_URL/healthz" >/dev/null; then break; fi
  sleep 1
done
curl -fsS "$STAGING_QDRANT_URL/healthz" >/dev/null

# Убедиться, что цель новая и пустая; не заменяйте URL на production/Compose Qdrant.
curl -fsS "$STAGING_QDRANT_URL/collections" | python3 -c 'import json,sys; collections=json.load(sys.stdin)["result"]["collections"]; assert not collections, "staging Qdrant is not empty"'
curl -fS -X POST "$STAGING_QDRANT_URL/collections/code_chunks/snapshots/upload?priority=snapshot" -F "snapshot=@$BACKUP_DIR/qdrant.snapshot"
curl -fsS "$STAGING_QDRANT_URL/collections/code_chunks"

# При выходе остановится только созданный временный контейнер.
```

Сверьте SHA-256 с manifest, database migration head и counts из `alembic-version.txt` / `postgres-row-counts.txt`; проверьте Qdrant points count против `qdrant-collection-info.json`, vector schema и sample points поколения READY-контракта. Этот одноразовый контейнер проверяет восстановление snapshot и его содержимое. Для проверки RAG подключайте staging API только к отдельной восстановленной staging-паре PostgreSQL/Qdrant; не перенастраивайте работающий Compose или production API на тестовую коллекцию. Проверьте получение актуальных chunks по `analysis_generation_id`, citations и чатовый запрос на тестовом контракте. Для Qdrant snapshot restore требуется версия той же или следующей minor; официальный upload/restore endpoint описан [здесь](https://api.qdrant.tech/api-reference/snapshots/recover-from-uploaded-snapshot).

На 2026-11-06 процедура была проверена локально на временной PostgreSQL базе (`pg_dump -Fc` → `pg_restore`, контрольная строка восстановлена) и временных Qdrant коллекциях (snapshot → upload, points count совпал; local Qdrant 1.20.0). Временные объекты удалены. Это проверка команд на local Compose, не production backup, не restore drill на реальном наборе данных и не подтверждение RPO/RTO.

## Типовые инциденты

### API показывает `degraded`

Проверить конкретное поле `/health`, затем container logs. Обратите внимание: Qdrant и Slither HTTP checks выполняются с timeout 5 секунд.

### Контракт остаётся в `indexing`

Проверить `celery-worker`, Redis broker, `celery_task_id`, `indexing_step` и время `indexing_heartbeat_at`. Для детерминированной сверки сначала выполнить dry-run, затем применить найденные terminal failures:

```bash
docker compose exec -T backend python -m app.scripts.reconcile_indexing
docker compose exec -T backend python -m app.scripts.reconcile_indexing --apply
```

По умолчанию stale считается попытка без heartbeat старше 3600 секунд; legacy-строки без heartbeat проверяются по `created_at`. Команду можно запускать с host cron каждые 15 минут. Она не делает автоматический retry: после `failed` пользователь повторно отправляет analyze. Не меняйте `status` напрямую. Детали состояний и восстановления приведены в [матрице фоновой индексации](./background-jobs.md#матрица-отказов-и-восстановление).

### `failed` на `compile` или `slither`

Смотреть `last_error`, наличие нужного `backend/.solc/solc-vVERSION`, health sidecar и его 90-секундный timeout. Slither report создаётся во временном каталоге и после запроса не сохраняется вне PostgreSQL.

### Чат отвечает `503`

Причины: отсутствует `OPENAI_API_KEY`, ошибка OpenAI-compatible endpoint, timeout 60 секунд, ошибка Qdrant или пустой некорректный ответ. Попадание в PostgreSQL/Redis cache может обслуживаться без LLM.

### Удаление оставило точки Qdrant

Удаление в Qdrant выполняется best effort: ошибка логируется, после чего PostgreSQL row всё равно удаляется. Нужного для автоматической очистки orphan points reconciliation job нет.

## Не реализовано

- zero-downtime deployment и автоматизированный/production-проверенный rollback;
- readiness/liveness endpoints с корректными HTTP statuses;
- backup/restore automation и disaster-recovery test;
- метрики очереди, latency, error rate и capacity alerts;
- graceful drain worker и documented task replay;
- data retention и удаление старых LLM reports;
- production sizing и benchmark pipeline.

См. также [установка](./setup.md) и [фоновые задачи](./background-jobs.md).
