#!/usr/bin/env bash

set -euo pipefail

_tbcaptial_verify_script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
_tbcaptial_verify_project_root="$(cd "${_tbcaptial_verify_script_dir}/.." && pwd -P)"

cd "${_tbcaptial_verify_project_root}"
. "${_tbcaptial_verify_script_dir}/activate_conda_env.sh"

exec python "${_tbcaptial_verify_script_dir}/verify_akshare_download.py" "$@"
