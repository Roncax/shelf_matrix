#!/usr/bin/env bash
# Install or update shelf-matrix on a Raspberry Pi. Run with sudo from the repo
# checkout: sudo deploy/install-pi.sh. Safe to re-run.
set -euo pipefail

if [[ ${EUID} -ne 0 ]]; then
    echo "This script must be run with sudo." >&2
    exit 1
fi

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LIB_REPO="${LIB_REPO:-https://github.com/hzeller/rpi-rgb-led-matrix}"
LIB_COMMIT="${LIB_COMMIT:-3da7a3b}"
LIB_DIR=/opt/rpi-rgb-led-matrix
APP_DIR=/opt/shelf-matrix
VENV="${APP_DIR}/venv"
CONF_DIR=/etc/shelf-matrix
STATE_DIR=/var/lib/shelf-matrix
LIB_MARKER="${VENV}/.rgbmatrix-commit"
BOOT_CONFIG=/boot/firmware/config.txt
BLACKLIST=/etc/modprobe.d/blacklist-rgb-matrix.conf
REBOOT_NEEDED=0

echo "==> Installing apt packages"
apt-get update
apt-get install -y git rsync python3-dev python3-venv python3-pil cython3 cmake build-essential

echo "==> Fetching rpi-rgb-led-matrix at ${LIB_COMMIT}"
if [[ ! -d "${LIB_DIR}/.git" ]]; then
    git clone "${LIB_REPO}" "${LIB_DIR}"
else
    git -C "${LIB_DIR}" remote set-url origin "${LIB_REPO}"
    git -C "${LIB_DIR}" fetch origin
fi
git -C "${LIB_DIR}" checkout --force "${LIB_COMMIT}"
LIB_HEAD="$(git -C "${LIB_DIR}" rev-parse HEAD)"

echo "==> Building C++ demo"
make -C "${LIB_DIR}/examples-api-use"

echo "==> Syncing shelf-matrix to ${APP_DIR}"
mkdir -p "${APP_DIR}"
rsync -a --delete \
    --exclude .git --exclude .venv --exclude .superpowers \
    --exclude tests --exclude deploy/.env --exclude venv \
    "${REPO_ROOT}/" "${APP_DIR}/"

echo "==> Creating virtualenv"
if [[ ! -x "${VENV}/bin/python" ]]; then
    python3 -m venv --system-site-packages "${VENV}"
fi

if [[ -f "${LIB_MARKER}" && "$(cat "${LIB_MARKER}")" == "${LIB_HEAD}" ]]; then
    echo "==> rgbmatrix bindings already built for ${LIB_HEAD}, skipping"
else
    echo "==> Building rgbmatrix Python bindings (slow on a Pi Zero)"
    "${VENV}/bin/pip" install "${LIB_DIR}"
    echo "${LIB_HEAD}" > "${LIB_MARKER}"
fi

echo "==> Installing shelf-matrix"
"${VENV}/bin/pip" install "${APP_DIR}[client]"

echo "==> Config and state"
mkdir -p "${CONF_DIR}" "${STATE_DIR}"
if [[ ! -f "${CONF_DIR}/config.toml" ]]; then
    install -m 600 "${REPO_ROOT}/deploy/config.example.toml" "${CONF_DIR}/config.toml"
    echo "Created ${CONF_DIR}/config.toml: edit server_url and token."
fi
chmod 600 "${CONF_DIR}/config.toml"

echo "==> Installing systemd unit"
install -m 644 "${REPO_ROOT}/deploy/shelf-matrix.service" /etc/systemd/system/shelf-matrix.service
systemctl daemon-reload
systemctl enable shelf-matrix
systemctl restart shelf-matrix

echo "==> Disabling onboard audio (conflicts with the matrix)"
if [[ ! -f "${BLACKLIST}" ]] || ! grep -qx 'blacklist snd_bcm2835' "${BLACKLIST}"; then
    echo 'blacklist snd_bcm2835' > "${BLACKLIST}"
    REBOOT_NEEDED=1
fi
if [[ -f "${BOOT_CONFIG}" ]] && ! grep -qE '^dtparam=audio=off[[:space:]]*$' "${BOOT_CONFIG}"; then
    if grep -qE '^dtparam=audio=' "${BOOT_CONFIG}"; then
        sed -i -E 's/^dtparam=audio=.*/dtparam=audio=off/' "${BOOT_CONFIG}"
    else
        echo 'dtparam=audio=off' >> "${BOOT_CONFIG}"
    fi
    REBOOT_NEEDED=1
fi

if [[ ${REBOOT_NEEDED} -eq 1 ]]; then
    echo "Reboot required for the audio changes to take effect."
fi
echo "Done."
