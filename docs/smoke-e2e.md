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

Она проверяет интеграции, отсутствующие в mocked smoke. Нужны подготовленные solc/Slither/embedding artifacts, работающий analyzer sidecar и локальный OpenAI-compatible endpoint с ключом (можно локальный сервис; платный API не обязателен):

1. Настройте локальный `.env`, включая `OPENAI_BASE_URL` и соответствующий `OPENAI_API_KEY`, если выбранный endpoint требует ключ. Не используйте реальные пользовательские данные.
2. Запустите `make up` и проверьте `make health`.
3. В интерфейсе откройте `http://localhost:3001`, зарегистрируйте временный demo-аккаунт и загрузите следующий исходник как `SmokeDemo.sol`:

   ```solidity
   pragma solidity ^0.8.24;

   contract SmokeDemo {
       function ping() public pure returns (uint256) {
           return 7;
       }
   }
   ```

4. Дождитесь `READY`; проверьте findings и advisory Risk Score notice, затем спросите: `What does ping return? Cite the source path and line range.` Убедитесь, что ответ содержит citations, скачайте PDF.
5. Удалите контракт и demo-аккаунт через интерфейс.

Этот ручной сценарий дополнительно затрагивает установленный solc, Slither sidecar, FastEmbed, Qdrant и реальную модель/endpoint. Они не выполняются в CI и не проверяются `make smoke-e2e`; реальный analyzer, embeddings и качество/grounding ответов внешней модели остаются за пределами автоматического smoke.
