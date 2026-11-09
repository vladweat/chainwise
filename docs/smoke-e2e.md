# Локальный smoke/E2E

`make smoke-e2e` запускает воспроизводимый API smoke для синтетического Solidity-контракта. Это mocked integration smoke через FastAPI `TestClient`, а не браузерный или полный live E2E.

## Запуск

Подготовьте offline-зависимости и локальное окружение по [инструкции установки](./setup.md), затем выполните:

```bash
make smoke-e2e
```

Команда поднимает PostgreSQL и Redis с ожиданием их healthcheck, затем запускает тест во временном backend-контейнере. Backend-тест использует отдельную БД `chainwise_test`; он не очищает рабочую БД. В конце тест удаляет синтетический контракт и оба demo-аккаунта через API. `make down` остановит зависимости после проверки.

Установленный Node.js нужен для проверки текста Risk Score notice из frontend. Ни `OPENAI_API_KEY`, ни реальные пользовательские данные не требуются.

## Что проверяется

- регистрация и login двух временных пользователей;
- отказ на повреждённом ZIP (`400`);
- upload небольшого `SmokeDemo.sol` (`202`) и polling до `ready` или явной ошибки;
- предел ожидания: не более 20 запросов статуса, не более 5 секунд и пауза 50 мс; автоматических повторных запусков анализа нет;
- findings, оценка `B` и attribution `heuristic`;
- `404` при чтении чужого контракта;
- исходник, контролируемый вопрос и непустые citations с путём файла и диапазоном строк, проверенным относительно загруженного текста;
- PDF с `application/pdf`, `%PDF-` заголовком и завершающим `%%EOF`;
- удаление контракта и demo-аккаунтов после чата.

API, SQLAlchemy/PostgreSQL, Redis-ограничители, upload handler, eager Celery task lifecycle, source/findings/chat/report routes и очистка данных выполняются настоящим кодом. Чтобы тест не зависел от локальных бинарников и внешних сервисов, compiler, Slither, Qdrant indexing/retrieval и LLM заменены детерминированными заглушками. Celery исполняет задачу синхронно. UI предупреждение проверяется отдельным Node test.

Backend CI job запускает `pytest tests/`, поэтому обнаруживает `test_smoke_e2e.py`. Команда `make smoke-e2e` дополнительно запускает frontend Node test для текста предупреждения.

## Необязательная ручная live-проверка

Тест `backend/tests/test_live_e2e.py` проходит тот же API flow с настоящими solc, Slither, FastEmbed и Qdrant; OpenAI-compatible chat mock-ается по умолчанию. Он запускается отдельно от быстрой mocked-проверки:

```bash
make deps                    # заранее подготавливает allowlisted solc и MiniLM
make up
make health
docker compose exec -T backend env \
  CHAINWISE_LIVE_E2E=1 \
  CHAINWISE_LIVE_E2E_MOCK_LLM=1 \
  python -m pytest tests/test_live_e2e.py -v
```

Тест использует синтетический `LiveDemo.sol`, выполняет compile, Slither, embeddings, Qdrant retrieval, Findings/Risk Score, tenant denial, citations и PDF; контракт также должен вызвать реальное Slither finding `immutable-states`. Негативный сценарий проверяет повреждённый ZIP. Celery задача исполняется eagerly; polling ограничен 120 секундами, analyzer HTTP calls — 20 секундами и одним коротким повтором при capacity 503, Qdrant client — 10 секундами. `finally` удаляет контракт и оба временных аккаунта, затем подтверждает, что они больше недоступны. Не запускайте этот тест против production базы.

Для единственного настоящего LLM-вызова задайте в `.env` ключ для выбранного OpenAI-compatible endpoint и удалите `CHAINWISE_LIVE_E2E_MOCK_LLM=1` из команды. Тест отправляет один вопрос. Не используйте ключ с платным бюджетом без установленного лимита. Тест может работать без внешнего endpoint с mock-ответом: в этом режиме retrieval, embeddings и citations настоящие, а только генерация текста заменена.

Негативный кейс, polling limit и tenant denial входят в автоматический live test; отдельный ручной UI прогон можно выполнить через `http://localhost:3001`, но он не является частью CI.

GitHub Actions не включает подготовку бинарников solc и FastEmbed и запускает live test как skipped; он выполняет быстрый mocked smoke. Поэтому CI не доказывает корректность установленных analyzer/model artifacts. Живой прогон фиксируется отдельно в [результатах проверок](./verification-results.md).
