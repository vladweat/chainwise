# Участие в разработке

## Подготовка

Следуйте [инструкции установки](./docs/setup.md). Основной поддерживаемый workflow использует Docker Compose и заранее подготовленные host-зависимости:

```bash
cp .env.example .env
make deps
make up
make health
```

Не добавляйте `.env`, токены, реальные API keys, исходники приватных контрактов или production dumps.

## Границы модулей

- `backend/app/api` — HTTP parsing, dependencies и response mapping;
- `backend/app/services` — orchestration и integrations;
- `backend/app/models` + `backend/alembic` — persistence;
- `backend/app/schemas` — внешние Pydantic contracts;
- `frontend/src/lib/api` — browser API client;
- `slither` — изолированный HTTP wrapper над Slither subprocess.

Существующий код — модульный монолит, не формальная Clean Architecture. Не скрывайте FastAPI/ORM coupling в документации.

## Изменения схемы и API

- Создавайте новую последовательную Alembic revision; не редактируйте уже применённую migration.
- Проверяйте upgrade и, где возможно, downgrade на disposable DB.
- Синхронизируйте Pydantic schemas, frontend types, tests и краткое описание в `docs/api.md`.
- Сохраняйте ownership filter и поведение `404` для чужих контрактов.
- Для новой конфигурации обновляйте `core/config.py`, `.env.example` и `docs/environment.md`.

## Проверки перед review

```bash
make lint
make test
cd frontend && npx tsc --noEmit
```

CI выполняет эти три класса проверок. `make test` измеряет coverage только `app.utils.pragma_parser` и `app.services.risk_scorer`; зелёный threshold не означает 80% всего backend. Для UI unit/E2E framework пока не настроен.

Добавляйте тесты для изменённого поведения, особенно для user isolation, auth cookies/CSRF, task idempotency, archive limits, cache и error paths внешних сервисов.

## Документация

Пишите по-русски, оставляя code symbols, API names и commands на английском. Источники истины:

- API — routes/schemas и runtime OpenAPI;
- DB — Alembic;
- env — `core/config.py` и Compose overrides;
- команды — Makefile и CI.

Обновляйте существующий документ вместо создания параллельного. Непроверенные сведения обозначайте как ограничение или вопрос.
