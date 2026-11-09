# Результаты проверок

Состояние на 2026-11-09. Этот отчёт различает GitHub CI для опубликованного commit и проверки текущих локальных изменений.

## GitHub Actions

[CI на `main`, commit `addef2e`](https://github.com/vladweat/chainwise/actions/runs/42585941882) завершился успешно. Все три job прошли: `backend-lint`, `frontend-typecheck` (включая TypeScript, frontend tests и Risk Score copy test), `backend-test` (PostgreSQL/Redis, pytest и focused coverage gate). Этот run подтверждает состояние опубликованной ветки; он не включает незакоммиченные изменения этого follow-up.

## Локальные проверки follow-up

| Проверка | Результат |
|---|---|
| `tests/test_analyzer_sidecar.py`, `test_indexing_lifecycle.py`, `test_indexing_lifecycle_postgres.py`, `test_smoke_e2e.py`, `test_live_e2e.py` | 35 passed, 1 skipped, 3 предупреждения Python dependency deprecations. PostgreSQL и Redis были настоящими временными сервисами; Qdrant integration test также выполнился против настоящего Qdrant. Пропуск — opt-in live E2E в обычном режиме. |
| `test_live_e2e.py` отдельно с `CHAINWISE_LIVE_E2E=1` | 1 passed за 6.73 сек.; настоящий solc 0.8.24, Slither 0.11.6 и finding `immutable-states`, FastEmbed 0.9.1 / MiniLM ONNX, PostgreSQL, Redis и Qdrant. LLM generation была mock-ирована; фактические embeddings, Qdrant retrieval и проверка citation path/line ranges — настоящие. |
| Analyzer overload: 8 одновременных HTTP `/analyze` запросов к поднятому sidecar | 2 ответили 200, 6 ответили 503 с `Retry-After: 5`; ошибочных HTTP статусов не было. Отдельный regression test фиксирует два занятых слота и retryable 503 третьему запросу. |
| Qdrant / cleanup | Live reconciliation test прошёл; после live E2E в тестовой БД не осталось demo users/contracts, а `code_chunks` содержал 0 точек. |
| `flake8` на изменённых Python модулях и тестах | Прошёл. |
| `systemd-analyze verify` для user service/timer | Прошёл. Timer добавлен и проверен на синтаксис; на этой машине он не устанавливался и не включался. |
| `docker compose config -q` | Прошёл для local Compose. Production overlay прошёл с фиктивными валидными deployment env значениями; `.env` не создавался. |
| `frontend/npm run test:risk-copy` | 4 passed, 0 failed. |
| `git diff --check` | Прошёл. |

XGBoost live model не запускался: свободного места не хватило для его Linux dependency wheel на NCCL. E2E выполнил штатный heuristic fallback, и API вернул attribution `heuristic`. Внешний LLM API не вызывался; настоящие grounding/response quality внешней модели этим прогоном не проверялись.

Live analyzer работал во временном тестовом Python контейнере, а не в Compose service с включёнными CPU/memory/PID, read-only filesystem и capability restrictions. Compose config проверен отдельно; runtime hardening этих параметров этим E2E не измерялся. Полный локальный `pytest tests/` не запускался; CI полный backend suite прошёл на указанном выше опубликованном commit.

В tracked repository нет UI screenshot assets. README и [architecture page](./architecture.md) содержат Mermaid-схему; реальные продуктовые screenshots пока не представлены.

## Повторение live API сценария

Подготовьте `.env`, `make deps`, затем поднимите Compose сервисы и выполните [инструкцию live E2E](./smoke-e2e.md). Для режима без внешнего ключа передайте `CHAINWISE_LIVE_E2E_MOCK_LLM=1`; тест всё равно выполняет настоящий Solidity compile, Slither analysis, embeddings и Qdrant retrieval.
