#!/usr/bin/env bash

# This file must be sourced so that `conda activate` changes the caller shell.

_tbcaptial_sourced=0
if [ -n "${ZSH_VERSION:-}" ]; then
    case "${ZSH_EVAL_CONTEXT:-}" in
        *:file) _tbcaptial_sourced=1 ;;
    esac
elif [ -n "${BASH_VERSION:-}" ]; then
    if [ "${BASH_SOURCE[0]}" != "$0" ]; then
        _tbcaptial_sourced=1
    fi
fi

if [ "${_tbcaptial_sourced}" -ne 1 ]; then
    printf '%s\n' 'This script must be sourced:' >&2
    printf '%s\n' '  source scripts/activate_conda_env.sh' >&2
    exit 1
fi

if [ -n "${ZSH_VERSION:-}" ]; then
    _tbcaptial_script_path="${(%):-%N}"
else
    _tbcaptial_script_path="${BASH_SOURCE[0]}"
fi

_tbcaptial_script_dir="$(cd "$(dirname "${_tbcaptial_script_path}")" && pwd -P)"
TBCAPTIAL_PROJECT_ROOT="$(cd "${_tbcaptial_script_dir}/.." && pwd -P)"
export TBCAPTIAL_PROJECT_ROOT

_tbcaptial_conda_executable=""
if command -v conda >/dev/null 2>&1; then
    _tbcaptial_conda_executable="$(command -v conda)"
else
    for _tbcaptial_candidate in \
        "${HOME}/miniforge3/bin/conda" \
        "${HOME}/mambaforge/bin/conda" \
        "${HOME}/anaconda3/bin/conda" \
        "/opt/homebrew/bin/conda" \
        "/usr/local/bin/conda"
    do
        if [ -x "${_tbcaptial_candidate}" ]; then
            _tbcaptial_conda_executable="${_tbcaptial_candidate}"
            break
        fi
    done
fi

if [ -z "${_tbcaptial_conda_executable}" ]; then
    printf '%s\n' 'Conda was not found. Run ./scripts/create_conda_env.sh first.' >&2
    unset _tbcaptial_sourced _tbcaptial_script_path _tbcaptial_script_dir
    unset _tbcaptial_conda_executable _tbcaptial_candidate
    return 1
fi

_tbcaptial_conda_base="$("${_tbcaptial_conda_executable}" info --base)"
if [ ! -f "${_tbcaptial_conda_base}/etc/profile.d/conda.sh" ]; then
    printf 'Conda shell hook is missing under %s\n' "${_tbcaptial_conda_base}" >&2
    unset _tbcaptial_sourced _tbcaptial_script_path _tbcaptial_script_dir
    unset _tbcaptial_conda_executable _tbcaptial_candidate _tbcaptial_conda_base
    return 1
fi

. "${_tbcaptial_conda_base}/etc/profile.d/conda.sh"
if ! conda activate tbcaptial; then
    printf '%s\n' 'Environment tbcaptial does not exist. Run ./scripts/create_conda_env.sh first.' >&2
    unset _tbcaptial_sourced _tbcaptial_script_path _tbcaptial_script_dir
    unset _tbcaptial_conda_executable _tbcaptial_candidate _tbcaptial_conda_base
    return 1
fi

printf 'Activated tbcaptial: Python %s (%s)\n' "$(python --version 2>&1)" "$(command -v python)"

unset _tbcaptial_sourced _tbcaptial_script_path _tbcaptial_script_dir
unset _tbcaptial_conda_executable _tbcaptial_candidate _tbcaptial_conda_base
