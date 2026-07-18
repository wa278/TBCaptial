#!/usr/bin/env bash

set -euo pipefail

readonly SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
readonly REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd -P)"

cd "${REPO_ROOT}"
source "${SCRIPT_DIR}/activate_conda_env.sh"
export PYTHONPATH="${REPO_ROOT}/src${PYTHONPATH:+:${PYTHONPATH}}"
exec python "${SCRIPT_DIR}/run_factor_backtests.py" "$@"
