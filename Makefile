.PHONY: venv solc embeddings venv-slither deps deps-frontend up up-fast up-frontend down build logs logs-frontend test lint health migrate train-risk

# Шаг 1 (нужен интернет): скачать зависимости backend + slither + frontend
deps: venv venv-slither deps-frontend

venv:
	bash backend/scripts/prepare-venv.sh

solc:
	backend/.venv/bin/python backend/scripts/prepare_solc.py

embeddings:
	backend/.venv/bin/python backend/scripts/prepare_embeddings.py

train-risk:
	backend/.venv/bin/python backend/scripts/train_risk_model.py

venv-slither:
	bash slither/scripts/prepare-venv.sh

deps-frontend:
	bash frontend/scripts/prepare-deps.sh

# Шаг 2 (offline): собрать и запустить контейнеры
up:
	docker compose up -d --build

# Запуск без пересборки образов (если диск тесный)
up-fast:
	docker compose up -d

# Пересобрать и перезапустить только frontend (после изменений UI)
up-frontend:
	docker compose build frontend && docker compose up -d frontend

build:
	docker compose build --no-cache

down:
	docker compose down

logs:
	docker compose logs -f backend frontend

logs-frontend:
	docker compose logs -f frontend

# pytest ходит в chainwise_test, не в рабочую БД
test:
	docker compose exec backend python -m pytest tests/ -v \
		--cov=app.utils.pragma_parser --cov=app.services.risk_scorer \
		--cov-fail-under=80 --cov-report=term-missing

lint:
	docker compose exec backend python -m flake8 app tests scripts

migrate:
	docker compose exec backend python -m alembic upgrade head

health:
	curl -s http://localhost:8001/health | python3 -m json.tool
