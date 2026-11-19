#!/usr/bin/env bash
# Standalone public release installer. Placeholders are filled by the manual release workflow.
set -Eeuo pipefail
export LC_ALL=C
REPOSITORY='@DISTRIBUTION_REPOSITORY@'
RELEASE_TAG='@RELEASE_TAG@'
BASE_ONLY=0
WORK_DIR=
cleanup() { if [[ -n "$WORK_DIR" && -d "$WORK_DIR" ]]; then rm -rf -- "$WORK_DIR"; fi; }
trap cleanup EXIT
die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }
for argument in "$@"; do
    case "$argument" in
        --base-only) BASE_ONLY=1 ;;
        --help|-h)
            printf '%s\n' 'Run on Ubuntu: sudo bash install.sh [--base-only]' \
                'Installs distribution prerequisites and application-update. No application/media routes are started.'
            exit 0 ;;
        *) die "Unknown argument: $argument" ;;
    esac
done
[[ "$(uname -s)" == Linux ]] || die 'This installer runs on the Ubuntu server only.'
((EUID == 0)) || die 'Run with sudo bash install.sh.'
source /etc/os-release
[[ "${ID:-}" == ubuntu && "${VERSION_ID:-}" == 24.04 ]] || die 'Supported profile: Ubuntu 24.04 LTS.'
[[ "$REPOSITORY" =~ ^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$ ]] || die 'Use the published release installer, not the source template.'
[[ "$RELEASE_TAG" =~ ^v[0-9]+\.[0-9]+\.[0-9]+([.-][A-Za-z0-9.-]+)?$ ]] || die 'Release tag is missing or invalid; use the published release installer.'
command -v flock >/dev/null || die 'Missing base OS command: flock.'
exec 9>/run/lock/application-install.lock
flock -n 9 || die 'Another application installation is running.'
if ! command -v curl >/dev/null || ! dpkg-query -W -f='${Status}' ca-certificates 2>/dev/null | grep -Fxq 'install ok installed'; then
    apt-get update
    apt-get --no-remove --no-install-recommends install -y curl ca-certificates
fi
WORK_DIR=$(mktemp -d /tmp/application-install.XXXXXXXX)
BASE_URL="https://github.com/$REPOSITORY/releases/download/$RELEASE_TAG"
download() {
    curl --fail --location --silent --show-error --proto '=https' --proto-redir '=https' \
        --tlsv1.2 --retry 3 --connect-timeout 15 --max-time 300 "$BASE_URL/$1" -o "$WORK_DIR/$1"
}
printf 'Downloading public release %s from %s (no Git authentication).\n' "$RELEASE_TAG" "$REPOSITORY"
download SHA256SUMS
FILES=(install.sh application-update.sh install-components.sh ubuntu-24.04.packages)
for file in "${FILES[@]}"; do
    download "$file"
    # Accept only the expected name and exactly one SHA-256, never paths supplied by a manifest.
    hash=$(awk -v name="$file" '$2 == name {print $1}' "$WORK_DIR/SHA256SUMS")
    [[ "$hash" =~ ^[0-9a-f]{64}$ ]] || die "Missing or invalid checksum for $file."
    actual=$(sha256sum -- "$WORK_DIR/$file")
    [[ "${actual%% *}" == "$hash" ]] || die "Checksum mismatch: $file"
done
# On-disk invocation also checks that this bootstrap matches the pinned release.
if [[ -f "${BASH_SOURCE[0]:-}" ]]; then
    cmp -s -- "${BASH_SOURCE[0]}" "$WORK_DIR/install.sh" || die 'Bootstrap differs from its published release.'
fi
RELEASE_DIR="/opt/converter/bootstrap/releases/$RELEASE_TAG"
install -d -m 0755 "$RELEASE_DIR/tools/install" "$RELEASE_DIR/packaging" /usr/local/sbin /var/lib/converter
install -m 0644 "$WORK_DIR/install.sh" "$RELEASE_DIR/install.sh"
install -m 0644 "$WORK_DIR/install-components.sh" "$RELEASE_DIR/tools/install/install-components.sh"
install -m 0644 "$WORK_DIR/ubuntu-24.04.packages" "$RELEASE_DIR/packaging/ubuntu-24.04.packages"
install -m 0644 "$WORK_DIR/SHA256SUMS" "$RELEASE_DIR/SHA256SUMS"
# Same-filesystem atomic replacement; the updater remains usable if prerequisites need manual attention.
UPDATE_TEMP=$(mktemp /usr/local/sbin/.application-update.XXXXXXXX)
if ! install -m 0755 "$WORK_DIR/application-update.sh" "$UPDATE_TEMP" || ! mv -f -- "$UPDATE_TEMP" /usr/local/sbin/application-update; then
    rm -f -- "$UPDATE_TEMP"
    die 'Could not install application-update.'
fi
ARGS=(--yes)
if ((BASE_ONLY)); then ARGS+=(--base-only); fi
status=0
bash "$RELEASE_DIR/tools/install/install-components.sh" "${ARGS[@]}" || status=$?
STATE_TEMP=$(mktemp /var/lib/converter/.bootstrap-release.XXXXXXXX)
printf '%s\n%s\n' "$RELEASE_TAG" "$status" > "$STATE_TEMP"
chmod 0644 "$STATE_TEMP"
mv -f -- "$STATE_TEMP" /var/lib/converter/bootstrap-release
printf '\napplication-update is installed. Future updates: application-update\n'
if ((status != 0)); then
    printf 'Prerequisites remain incomplete (status %s). Follow the messages above and rerun application-update.\n' "$status" >&2
fi
printf '%s\n' 'This release prepares system prerequisites only; the Converter application and NDI packages are not yet included.'
exit "$status"
