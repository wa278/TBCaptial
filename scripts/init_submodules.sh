#!/usr/bin/env bash

set -euo pipefail

readonly EXPECTED_COMMIT="2924e0cff36669a3563ffb5cb139da0ba9254045"
readonly EXPECTED_REMOTE="git@github.com:wa278/akquant.git"
readonly SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
readonly REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd -P)"
readonly SUBMODULE_PATH="third_party/akquant"
readonly SOURCE_DIR="${REPO_ROOT}/${SUBMODULE_PATH}"

main() {
    local configured_remote source_commit source_remote source_status

    configured_remote="$(
        git -C "${REPO_ROOT}" config --file .gitmodules \
            --get "submodule.${SUBMODULE_PATH}.url"
    )"
    if [[ "${configured_remote}" != "${EXPECTED_REMOTE}" ]]; then
        printf 'AKQuant submodule URL mismatch: expected %s, got %s\n' \
            "${EXPECTED_REMOTE}" "${configured_remote:-<none>}" >&2
        return 1
    fi

    git -C "${REPO_ROOT}" submodule sync -- "${SUBMODULE_PATH}"
    git -C "${REPO_ROOT}" submodule update --init --recursive -- "${SUBMODULE_PATH}"

    source_commit="$(git -C "${SOURCE_DIR}" rev-parse HEAD)"
    source_remote="$(git -C "${SOURCE_DIR}" remote get-url origin)"
    source_status="$(git -C "${SOURCE_DIR}" status --porcelain)"
    if [[ "${source_commit}" != "${EXPECTED_COMMIT}" ]]; then
        printf 'AKQuant submodule commit mismatch: expected %s, got %s\n' \
            "${EXPECTED_COMMIT}" "${source_commit}" >&2
        return 1
    fi
    if [[ "${source_remote}" != "${EXPECTED_REMOTE}" ]]; then
        printf 'AKQuant submodule remote mismatch: expected %s, got %s\n' \
            "${EXPECTED_REMOTE}" "${source_remote}" >&2
        return 1
    fi
    if [[ -n "${source_status}" ]]; then
        printf 'AKQuant submodule has local changes; refusing dirty source.\n' >&2
        printf '%s\n' "${source_status}" >&2
        return 1
    fi

    printf 'AKQuant submodule ready: %s @ %s\n' \
        "${EXPECTED_REMOTE}" "${EXPECTED_COMMIT}"
}

main "$@"
