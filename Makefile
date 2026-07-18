SHELL := /bin/bash

.PHONY: setup submodules env env-verify akquant-backend akshare-complete akshare-download akshare-preview akshare-summary akshare-verify factor-backtests test quality acceptance

AKSHARE_ARGS ?=
AKSHARE_COMPLETE_ARGS ?=
AKSHARE_PREVIEW_ARGS ?=
AKSHARE_SUMMARY_ARGS ?=
AKSHARE_VERIFY_ARGS ?=
FACTOR_BACKTEST_ARGS ?=

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

akshare-complete:
	./scripts/download_akshare_complete.sh $(AKSHARE_COMPLETE_ARGS)

akshare-preview:
	./scripts/preview_akshare_data.sh $(AKSHARE_PREVIEW_ARGS)

akshare-summary:
	./scripts/summarize_akshare_data.sh $(AKSHARE_SUMMARY_ARGS)

akshare-verify:
	./scripts/verify_akshare_download.sh $(AKSHARE_VERIFY_ARGS)

factor-backtests:
	./scripts/run_factor_backtests.sh $(FACTOR_BACKTEST_ARGS)

test:
	source scripts/activate_conda_env.sh && pytest

quality:
	source scripts/activate_conda_env.sh && ruff check .
	source scripts/activate_conda_env.sh && ruff format --check .
	source scripts/activate_conda_env.sh && mypy src tests

acceptance: submodules env-verify test quality
