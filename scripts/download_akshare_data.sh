#!/usr/bin/env bash

set -euo pipefail

_tbcaptial_download_script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
_tbcaptial_download_project_root="$(cd "${_tbcaptial_download_script_dir}/.." && pwd -P)"

cd "${_tbcaptial_download_project_root}"
. "${_tbcaptial_download_script_dir}/activate_conda_env.sh"

exec python "${_tbcaptial_download_script_dir}/download_akshare_data.py" "$@"
