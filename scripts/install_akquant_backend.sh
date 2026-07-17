#!/usr/bin/env bash

set -euo pipefail

readonly ENV_NAME="tbcaptial"
readonly EXPECTED_VERSION="0.3.2"
readonly EXPECTED_COMMIT="2924e0cff36669a3563ffb5cb139da0ba9254045"
readonly EXPECTED_REMOTE="git@github.com:wa278/akquant.git"
readonly SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
readonly REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd -P)"
readonly SOURCE_DIR="${REPO_ROOT}/third_party/akquant"
readonly WHEEL_STORE="${REPO_ROOT}/var/vendor/akquant"
AKQUANT_BUILD_DIR=""

cleanup_build() {
    if [[ -n "${AKQUANT_BUILD_DIR}" ]] && [[ -d "${AKQUANT_BUILD_DIR}" ]]; then
        rm -rf -- "${AKQUANT_BUILD_DIR}"
    fi
}

find_conda_executable() {
    local candidate

    if command -v conda >/dev/null 2>&1; then
        command -v conda
        return 0
    fi

    for candidate in \
        "${HOME}/miniforge3/bin/conda" \
        "${HOME}/mambaforge/bin/conda" \
        "${HOME}/anaconda3/bin/conda" \
        "/opt/homebrew/bin/conda" \
        "/usr/local/bin/conda"
    do
        if [[ -x "${candidate}" ]]; then
            printf '%s\n' "${candidate}"
            return 0
        fi
    done

    return 1
}

sha256_file() {
    local path="$1"

    if command -v shasum >/dev/null 2>&1; then
        shasum -a 256 "${path}" | awk '{print $1}'
    elif command -v sha256sum >/dev/null 2>&1; then
        sha256sum "${path}" | awk '{print $1}'
    else
        printf '%s\n' "Neither shasum nor sha256sum is available." >&2
        return 1
    fi
}

main() {
    local conda_executable python_executable source_commit source_epoch source_remote
    local source_status
    local wheel_path wheel_name wheel_sha
    local -a wheels

    if [[ ! -f "${SOURCE_DIR}/Cargo.toml" ]] || \
        ! git -C "${SOURCE_DIR}" rev-parse --git-dir >/dev/null 2>&1
    then
        printf 'AKQuant source repository not found: %s\n' "${SOURCE_DIR}" >&2
        printf 'Run ./scripts/init_submodules.sh first.\n' >&2
        return 1
    fi

    source_commit="$(git -C "${SOURCE_DIR}" rev-parse HEAD)"
    source_epoch="$(git -C "${SOURCE_DIR}" show -s --format=%ct "${EXPECTED_COMMIT}")"
    source_remote="$(git -C "${SOURCE_DIR}" remote get-url origin)"
    source_status="$(git -C "${SOURCE_DIR}" status --porcelain)"

    if [[ "${source_commit}" != "${EXPECTED_COMMIT}" ]]; then
        printf 'AKQuant commit mismatch: expected %s, got %s\n' \
            "${EXPECTED_COMMIT}" "${source_commit}" >&2
        return 1
    fi
    if [[ "${source_remote}" != "${EXPECTED_REMOTE}" ]]; then
        printf 'AKQuant remote mismatch: expected %s, got %s\n' \
            "${EXPECTED_REMOTE}" "${source_remote}" >&2
        return 1
    fi
    if [[ -n "${source_status}" ]]; then
        printf 'AKQuant source has uncommitted changes; refusing reproducible build.\n' >&2
        printf '%s\n' "${source_status}" >&2
        return 1
    fi

    if ! conda_executable="$(find_conda_executable)"; then
        printf '%s\n' 'Conda was not found. Run ./scripts/create_conda_env.sh first.' >&2
        return 1
    fi
    python_executable="$(
        "${conda_executable}" run --name "${ENV_NAME}" \
            python -c 'import sys; print(sys.executable)'
    )"

    AKQUANT_BUILD_DIR="$(mktemp -d "${TMPDIR:-/tmp}/tbcaptial-akquant-build.XXXXXX")"
    trap cleanup_build EXIT

    printf 'Building AKQuant %s (%s) from %s\n' \
        "${EXPECTED_VERSION}" "${EXPECTED_COMMIT}" "${SOURCE_DIR}"
    SOURCE_DATE_EPOCH="${source_epoch}" \
        "${conda_executable}" run --no-capture-output --name "${ENV_NAME}" \
        maturin build \
        --manifest-path "${SOURCE_DIR}/Cargo.toml" \
        --release \
        --locked \
        --interpreter "${python_executable}" \
        --out "${AKQUANT_BUILD_DIR}"

    wheels=("${AKQUANT_BUILD_DIR}"/akquant-${EXPECTED_VERSION}-*.whl)
    if [[ "${#wheels[@]}" -ne 1 ]] || [[ ! -f "${wheels[0]}" ]]; then
        printf 'Expected exactly one AKQuant wheel, found %s.\n' "${#wheels[@]}" >&2
        return 1
    fi

    wheel_path="${wheels[0]}"
    wheel_name="$(basename "${wheel_path}")"
    wheel_sha="$(sha256_file "${wheel_path}")"

    "${conda_executable}" run --no-capture-output --name "${ENV_NAME}" \
        python -m pip install --no-deps --force-reinstall "${wheel_path}"

    mkdir -p "${WHEEL_STORE}"
    install -m 0644 "${wheel_path}" "${WHEEL_STORE}/${wheel_name}"

    "${conda_executable}" run --no-capture-output --name "${ENV_NAME}" python -c \
        "import akquant; assert akquant.__version__ == '${EXPECTED_VERSION}', akquant.__version__; print('AKQuant import PASS:', akquant.__version__)"

    printf 'AKQuant wheel: %s\n' "${WHEEL_STORE}/${wheel_name}"
    printf 'AKQuant wheel SHA-256: %s\n' "${wheel_sha}"
}

main "$@"
