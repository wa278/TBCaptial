SHELL := /bin/bash

.PHONY: setup env env-verify akquant-backend akshare-download akshare-verify test quality acceptance

AKSHARE_ARGS ?=
AKSHARE_VERIFY_ARGS ?=

setup: env akquant-backend

env:
	./scripts/create_conda_env.sh

env-verify:
	source scripts/activate_conda_env.sh && python scripts/verify_conda_env.py

akquant-backend:
	./scripts/install_akquant_backend.sh

akshare-download:
	./scripts/download_akshare_data.sh $(AKSHARE_ARGS)

akshare-verify:
	./scripts/verify_akshare_download.sh $(AKSHARE_VERIFY_ARGS)

test:
	source scripts/activate_conda_env.sh && pytest

quality:
	source scripts/activate_conda_env.sh && ruff check src tests
	source scripts/activate_conda_env.sh && ruff format --check src tests
	source scripts/activate_conda_env.sh && mypy src tests

acceptance: env-verify test quality
