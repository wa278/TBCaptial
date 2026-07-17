SHELL := /bin/bash

.PHONY: setup submodules env env-verify akquant-backend akshare-download akshare-preview akshare-verify test quality acceptance

AKSHARE_ARGS ?=
AKSHARE_PREVIEW_ARGS ?=
AKSHARE_VERIFY_ARGS ?=

setup:
	./scripts/init_submodules.sh
	./scripts/create_conda_env.sh
	./scripts/install_akquant_backend.sh

submodules:
	./scripts/init_submodules.sh

env:
	./scripts/create_conda_env.sh

env-verify:
	source scripts/activate_conda_env.sh && python scripts/verify_conda_env.py

akquant-backend: submodules
	./scripts/install_akquant_backend.sh

akshare-download:
	./scripts/download_akshare_data.sh $(AKSHARE_ARGS)

akshare-preview:
	./scripts/preview_akshare_data.sh $(AKSHARE_PREVIEW_ARGS)

akshare-verify:
	./scripts/verify_akshare_download.sh $(AKSHARE_VERIFY_ARGS)

test:
	source scripts/activate_conda_env.sh && pytest

quality:
	source scripts/activate_conda_env.sh && ruff check .
	source scripts/activate_conda_env.sh && ruff format --check .
	source scripts/activate_conda_env.sh && mypy src tests

acceptance: submodules env-verify test quality
