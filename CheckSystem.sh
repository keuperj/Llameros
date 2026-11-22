#!/usr/bin/env bash
# Ubuntu preflight for Llameros. Reports only; no installs or service changes.
set -u
export LC_ALL=C

usage() {
    cat <<'EOF'
Usage: ./CheckSystem.sh [--gpu auto|cpu|nvidia|amd] [--allow-rootful]

Run as the account that will run Llameros, without sudo. Checks the active
Docker endpoint but does not start containers, pull images, or download Python.
GPU checks are advisory in auto mode and required for an explicit GPU vendor.
--allow-rootful only acknowledges this check; it does not change Llameros config.

Exit status: 0 = no blockers, 1 = blockers found, 2 = invalid arguments.
EOF
}

gpu=auto
allow_rootful=false
while (($#)); do
    case $1 in
        -h|--help) usage; exit 0 ;;
        --allow-rootful) allow_rootful=true; shift ;;
        --gpu)
            case ${2:-} in
                auto|cpu|nvidia|amd) gpu=$2; shift 2 ;;
                *) usage >&2; exit 2 ;;
            esac ;;
        *) usage >&2; exit 2 ;;
    esac
done

cd -- "$(dirname -- "${BASH_SOURCE[0]}")" || exit 1
failures=0
warnings=0
ok() { printf '[OK]   %s\n' "$*"; }
note() { printf '[INFO] %s\n' "$*"; }
hint() { printf '       Hint: %s\n' "$*"; }
fail() { printf '[FAIL] %s\n' "$*"; ((++failures)); }
warn() { printf '[WARN] %s\n' "$*"; ((++warnings)); }
has() { command -v "$1" >/dev/null 2>&1; }
probe() { timeout --kill-after=2s 10s "$@"; }

printf 'Llameros system preflight (Ubuntu)\n\n'
if [[ -r /etc/os-release ]] && grep -Eq '^ID="?ubuntu"?$' /etc/os-release; then
    ok "$(sed -n 's/^PRETTY_NAME=//p' /etc/os-release | tr -d '"') ($(uname -m))"
else
    warn 'Not identified as Ubuntu; the package hints below are Ubuntu-specific.'
fi
if [[ $(id -u) == 0 ]]; then
    fail 'Running as root does not test the Llameros service account.'
    hint 'Log in as the intended user and run ./CheckSystem.sh without sudo.'
    exit 1
fi

printf '\nPython and login dependencies\n'
python=''
if has uv && uv_version=$(probe uv --version 2>/dev/null); then
    ok "$uv_version"
    python=$(probe uv --offline --no-cache python find --no-python-downloads 2>/dev/null) || python=''
else
    fail 'uv is missing from PATH or could not run.'
    hint 'Install uv for your user (see install.md); if installed in ~/.local/bin, add that directory to PATH.'
    python=$(command -v python3) || python=''
fi
if [[ -n $python ]] && py_version=$(probe "$python" -I -B -c \
    'import sys; print(sys.version.split()[0]); sys.exit(sys.version_info < (3, 11))' 2>/dev/null); then
    ok "Python $py_version ($python)"
else
    fail 'No usable Python 3.11+ found for the checkout.'
    hint 'Run uv python install 3.12 after installing uv, or install a supported Ubuntu python3. Check .python-version or UV_PYTHON if discovery still fails.'
    python=''
fi

if [[ -n $python ]]; then
    if probe "$python" -I -B -c 'from ctypes import CDLL; CDLL("libpam.so.0")' >/dev/null 2>&1; then
        ok 'Python can load the system PAM library.'
    else
        fail 'Python cannot load libpam.so.0.'
        hint 'Install or repair Ubuntu libpam0g.'
    fi
elif probe /sbin/ldconfig -p 2>/dev/null | grep 'libpam\.so\.0 ' >/dev/null; then
    note 'PAM is listed in the linker cache; Python loading could not be checked.'
else
    warn 'PAM could not be verified without Python.'
    hint 'Check that Ubuntu libpam0g is installed.'
fi
if [[ -r /etc/pam.d/login ]]; then
    ok 'Default PAM login policy is present (password authentication not tested).'
else
    fail 'Default PAM policy /etc/pam.d/login is not readable.'
    hint 'Ask the administrator to restore the Ubuntu login policy or configure Llameros to use an appropriate PAM service.'
fi
if [[ ! -s /etc/ssl/certs/ca-certificates.crt ]]; then
    warn 'Ubuntu CA certificate bundle was not found.'
    hint 'Install or repair ca-certificates if HTTPS downloads or registry access fail.'
fi

printf '\nDocker\n'
docker_mode=unknown
nvidia_runtime=''
cdi_dirs=''
if ! has docker; then
    fail 'Docker CLI is missing.'
    hint 'Install Docker Engine and CLI for Ubuntu, then set up rootless Docker (see install.md).'
else
    # Docker context takes precedence over DOCKER_HOST, matching the Docker CLI.
    endpoint=${DOCKER_HOST:-}
    if [[ -n ${DOCKER_CONTEXT:-} || -z $endpoint ]]; then
        endpoint=$(probe docker context inspect --format '{{.Endpoints.docker.Host}}' 2>/dev/null) || endpoint=''
    fi
    if [[ -z $endpoint ]]; then
        fail 'Could not resolve the active Docker endpoint.'
        hint 'Run docker context ls; check DOCKER_CONTEXT and DOCKER_HOST for stale overrides.'
    elif [[ $endpoint != unix:///* ]]; then
        fail 'The selected Docker endpoint is not a local Unix socket. Llameros does not support remote Docker.'
        hint 'Select the local rootless context and remove stale environment overrides.'
    elif daemon=$(probe docker info --format '{{.ServerVersion}}|{{join .SecurityOptions ","}}|{{if index .Runtimes "nvidia"}}yes{{else}}no{{end}}') && [[ $daemon == *'|'*'|'* ]]; then
        IFS='|' read -r version security nvidia_runtime <<< "$daemon"
        if [[ ,$security, == *,name=rootless,* ]]; then
            docker_mode=rootless
            ok "Docker Engine $version: rootless ($endpoint)"
        else
            docker_mode=rootful
            if $allow_rootful; then
                warn "Docker Engine $version: rootful ($endpoint). Access is root-equivalent."
                hint 'To use this daemon, explicitly set allow_rootful_docker = true in the loaded Llameros config.'
            else
                fail "Docker Engine $version: rootful ($endpoint). Llameros requires rootless Docker by default."
                hint 'Switch to rootless Docker, or see the rootful opt-in in install.md before using --allow-rootful.'
            fi
        fi
    else
        fail "Cannot reach Docker at $endpoint as the current user."
        hint 'Check systemctl --user status docker.service and docker context ls. Do not use sudo or make the socket world-writable.'
    fi
fi

if [[ $docker_mode != rootless ]] && ! $allow_rootful; then
    if ! has newuidmap || ! has newgidmap; then
        note 'Rootless setup helpers newuidmap/newgidmap are missing. On Ubuntu: sudo apt install uidmap.'
    fi
    if ! has dockerd-rootless-setuptool.sh; then
        note 'Rootless setup tool not found; for Docker CE, install docker-ce-rootless-extras from Docker’s Ubuntu repository.'
    fi
    username=$(id -un)
    uid=$(id -u)
    for file in /etc/subuid /etc/subgid; do
        if ! awk -F: -v name="$username" -v uid="$uid" \
            '($1 == name || $1 == uid) && $3 >= 65536 { found=1 } END { exit !found }' "$file" 2>/dev/null; then
            note "No local 65,536-ID range found for $username in $file. Ask the administrator to check subordinate ID allocation."
        fi
    done
fi
if has systemctl && probe systemctl --user show-environment >/dev/null 2>&1; then
    ok 'systemd user manager is reachable.'
else
    warn 'systemd user manager is not reachable; boot startup and dashboard Docker restart may not work.'
    hint 'Use a normal SSH/desktop login session for the service account; on Ubuntu, check dbus-user-session.'
fi

printf '\nGPU (mode: %s)\n' "$gpu"
gpu_issue() { if [[ $gpu == auto ]]; then warn "$@"; else fail "$@"; fi; }
gpu_detected=false
# Identify display/accelerator devices, not unrelated PCI functions such as HDMI audio.
pci_vendor() {
    local device vendor class
    for device in /sys/bus/pci/devices/*; do
        [[ -r $device/vendor && -r $device/class ]] || continue
        read -r vendor < "$device/vendor"
        read -r class < "$device/class"
        if [[ $vendor == "$1" && ( $class == 0x03* || $class == 0x12* ) ]]; then return 0; fi
    done
    return 1
}

if [[ $gpu == nvidia ]] || { [[ $gpu == auto ]] && { has nvidia-smi || pci_vendor 0x10de; }; }; then
    gpu_detected=true
    if has nvidia-smi && devices=$(probe nvidia-smi -L 2>/dev/null) && [[ $devices == *'UUID: GPU-'* ]]; then
        ok 'NVIDIA driver exposes GPU UUIDs on the host.'
        printf '%s\n' "$devices"
    else
        gpu_issue 'nvidia-smi is missing, failed, or found no GPU UUIDs.'
        hint 'Check the NVIDIA driver with ubuntu-drivers devices or your GPU vendor’s Ubuntu instructions. Use the vendor driver packages for systems such as DGX.'
    fi
    if has nvidia-ctk && toolkit=$(probe nvidia-ctk --version 2>/dev/null); then
        ok "${toolkit%%$'\n'*}"
        if cdi=$(probe nvidia-ctk cdi list 2>/dev/null) && [[ $cdi == *'nvidia.com/gpu='* ]]; then
            ok 'NVIDIA CDI devices are listed on the host (this does not prove Docker can resolve them).'
        else
            warn 'No NVIDIA CDI devices listed. Legacy GPU configurations can still work.'
            hint 'For CDI setups, check nvidia-cdi-refresh.service and the rootless NVIDIA instructions in install.md.'
        fi
    else
        gpu_issue 'NVIDIA Container Toolkit CLI (nvidia-ctk) is missing or not working.'
        hint 'Set up NVIDIA’s apt repository and install nvidia-container-toolkit; follow the Toolkit guide linked in install.md.'
    fi
    if [[ $docker_mode != unknown ]]; then
        cdi_dirs=$(probe docker info --format '{{json .CDISpecDirs}}' 2>/dev/null) || cdi_dirs=''
        [[ -z $cdi_dirs || $cdi_dirs == null || $cdi_dirs == '[]' ]] || note "Docker CDI search paths: $cdi_dirs"
        if [[ $nvidia_runtime == yes ]]; then
            ok 'Docker lists the NVIDIA runtime.'
        else
            note 'No named NVIDIA runtime in Docker; CDI/device requests may still work.'
        fi
    fi
    hint 'Container GPU access is not tested here. Run the NVIDIA container startup check in install.md to verify it.'
fi

if [[ $gpu == amd ]] || { [[ $gpu == auto ]] && { [[ -c /dev/kfd ]] || pci_vendor 0x1002; }; }; then
    gpu_detected=true
    render_ready=false
    for node in /sys/class/drm/renderD*; do
        render=/dev/dri/${node##*/}
        if [[ -r $node/device/vendor && $(<"$node/device/vendor") == 0x1002 && -c $render && -r $render && -w $render ]]; then
            render_ready=true
        fi
    done
    if [[ -c /dev/kfd && -r /dev/kfd && -w /dev/kfd ]] && $render_ready; then
        ok 'AMD /dev/kfd and an AMD render device are accessible to this user (container ROCm not tested).'
    else
        gpu_issue 'AMD /dev/kfd or an accessible AMD /dev/dri/renderD* device is missing.'
        hint 'Check AMDGPU/ROCm hardware and Ubuntu support; ask the administrator about render/video group access, then log in again.'
    fi
fi
if [[ $gpu == cpu ]]; then
    note 'GPU checks skipped (CPU mode).'
elif ! $gpu_detected; then
    note 'No supported GPU detected; NVIDIA and AMD components are not needed for CPU mode.'
fi

printf '\nSummary: %s blocker(s), %s warning(s).\n' "$failures" "$warnings"
if ((failures)); then
    exit 1
fi
note 'No blockers found by this preflight. Run uv sync --locked to install the project dependencies.'