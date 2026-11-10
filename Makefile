.PHONY: setup dev run test test-models eval lint build

setup:
	./start.sh --setup
dev:
	./start.sh --dev
run:
	./start.sh
test:
	cd backend && ../.venv/bin/python -m pytest
test-models:
	cd backend && ../.venv/bin/python -m pytest -m models
eval:
	cd backend && ../.venv/bin/python scripts/eval_alignment.py
lint:
	cd backend && ../.venv/bin/python -m ruff check app tests scripts
build:
	cd frontend && npm run build
