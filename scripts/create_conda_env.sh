#!/usr/bin/env bash

set -euo pipefail

readonly ENV_NAME="tbcaptial"
readonly MINIFORGE_VERSION="26.3.2-2"
readonly SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
readonly REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd -P)"
readonly ENV_FILE="${REPO_ROOT}/environment.yml"
readonly VERIFY_SCRIPT="${SCRIPT_DIR}/verify_conda_env.py"

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

bootstrap_miniforge() {
    local os_name machine installer_platform installer_arch installer_name
    local release_url install_prefix temp_dir installer_path checksum_path
    local expected_sha actual_sha asset_name output_path

    os_name="$(uname -s)"
    machine="$(uname -m)"

    case "${os_name}" in
        Darwin) installer_platform="MacOSX" ;;
        Linux) installer_platform="Linux" ;;
        *)
            printf 'Automatic Miniforge bootstrap is unsupported on %s.\n' "${os_name}" >&2
            printf 'Install Miniforge manually, then rerun this script.\n' >&2
            return 1
            ;;
    esac

    case "${machine}" in
        x86_64) installer_arch="x86_64" ;;
        arm64) installer_arch="arm64" ;;
        aarch64) installer_arch="aarch64" ;;
        *)
            printf 'Automatic Miniforge bootstrap is unsupported on architecture %s.\n' \
                "${machine}" >&2
            return 1
            ;;
    esac

    installer_name="Miniforge3-${MINIFORGE_VERSION}-${installer_platform}-${installer_arch}.sh"
    release_url="https://github.com/conda-forge/miniforge/releases/download/${MINIFORGE_VERSION}"
    install_prefix="${TBCAPTIAL_MINIFORGE_PREFIX:-${HOME}/miniforge3}"

    if [[ -e "${install_prefix}" ]]; then
        printf 'Cannot bootstrap Miniforge: target already exists: %s\n' "${install_prefix}" >&2
        printf 'Set TBCAPTIAL_MINIFORGE_PREFIX to an unused directory or fix that installation.\n' >&2
        return 1
    fi

    temp_dir="$(mktemp -d "${TMPDIR:-/tmp}/tbcaptial-miniforge.XXXXXX")"
    installer_path="${temp_dir}/${installer_name}"
    checksum_path="${installer_path}.sha256"

    cleanup_bootstrap() {
        rm -rf "${temp_dir}"
    }
    trap cleanup_bootstrap RETURN

    printf 'Conda was not found. Bootstrapping Miniforge %s into %s\n' \
        "${MINIFORGE_VERSION}" "${install_prefix}"
    for asset_name in "${installer_name}" "${installer_name}.sha256"; do
        output_path="${temp_dir}/${asset_name}"
        if command -v curl >/dev/null 2>&1 && curl \
            --fail \
            --location \
            --retry 3 \
            --connect-timeout 20 \
            --max-time 300 \
            --output "${output_path}" \
            "${release_url}/${asset_name}"
        then
            continue
        fi

        if command -v gh >/dev/null 2>&1; then
            rm -f "${output_path}"
            gh release download "${MINIFORGE_VERSION}" \
                --repo conda-forge/miniforge \
                --pattern "${asset_name}" \
                --dir "${temp_dir}"
            continue
        fi

        printf 'Unable to download %s: install curl or GitHub CLI and retry.\n' \
            "${asset_name}" >&2
        return 1
    done

    expected_sha="$(awk 'NR == 1 {print $1}' "${checksum_path}")"
    actual_sha="$(sha256_file "${installer_path}")"
    if [[ "${#expected_sha}" -ne 64 ]] || [[ "${expected_sha}" =~ [^[:xdigit:]] ]]; then
        printf 'Invalid checksum file received for %s.\n' "${installer_name}" >&2
        return 1
    fi
    if [[ "${actual_sha}" != "${expected_sha}" ]]; then
        printf 'Miniforge checksum mismatch. Expected %s, got %s.\n' \
            "${expected_sha}" "${actual_sha}" >&2
        return 1
    fi

    printf 'Installer SHA-256 verified: %s\n' "${actual_sha}"
    bash "${installer_path}" -b -p "${install_prefix}"
    printf '%s\n' "${install_prefix}/bin/conda"
}

main() {
    local conda_executable

    if [[ ! -f "${ENV_FILE}" ]]; then
        printf 'Missing environment declaration: %s\n' "${ENV_FILE}" >&2
        return 1
    fi

    if conda_executable="$(find_conda_executable)"; then
        printf 'Using Conda: %s\n' "${conda_executable}"
    else
        conda_executable="$(bootstrap_miniforge | tail -n 1)"
    fi

    export CONDA_CHANNEL_PRIORITY=strict

    "${conda_executable}" --version
    if "${conda_executable}" env list | awk -v name="${ENV_NAME}" '$1 == name {found=1} END {exit !found}'; then
        printf 'Updating Conda environment %s from %s\n' "${ENV_NAME}" "${ENV_FILE}"
        "${conda_executable}" env update \
            --name "${ENV_NAME}" \
            --file "${ENV_FILE}" \
            --prune
    else
        printf 'Creating Conda environment %s from %s\n' "${ENV_NAME}" "${ENV_FILE}"
        "${conda_executable}" env create --file "${ENV_FILE}"
    fi

    printf 'Running offline environment smoke test...\n'
    "${conda_executable}" run --no-capture-output --name "${ENV_NAME}" \
        python "${VERIFY_SCRIPT}"

    printf '\nEnvironment %s is ready. Activate it with:\n' "${ENV_NAME}"
    printf '  source scripts/activate_conda_env.sh\n'
}

main "$@"
