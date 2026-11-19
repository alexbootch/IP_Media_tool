#!/usr/bin/env bash
# Installed as /usr/local/sbin/application-update; no Git checkout or credentials.
set -Eeuo pipefail
export LC_ALL=C
REPOSITORY='@DISTRIBUTION_REPOSITORY@'
MODE=update
VERSION=
BASE_ONLY=0
WORK_DIR=
die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }
cleanup() { if [[ -n "$WORK_DIR" && -d "$WORK_DIR" ]]; then rm -rf -- "$WORK_DIR"; fi; }
trap cleanup EXIT
while (($#)); do
    case "$1" in
        --check) MODE=check; shift ;;
        --base-only) BASE_ONLY=1; shift ;;
        --version) (($# >= 2)) || die '--version requires a tag.'; VERSION=$2; shift 2 ;;
        --help|-h)
            printf '%s\n' 'application-update [--check] [--version vX.Y.Z] [--base-only]' \
                'Downloads a public release; no Git login. No Converter services or media pipelines are started.'
            exit 0 ;;
        *) die "Unknown argument: $1" ;;
    esac
done
[[ "$REPOSITORY" =~ ^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$ ]] || die 'Unconfigured updater template.'
if [[ -n "$VERSION" ]]; then
    [[ "$VERSION" =~ ^v[0-9]+\.[0-9]+\.[0-9]+([.-][A-Za-z0-9.-]+)?$ ]] || die 'Invalid release tag.'
fi
if [[ "$MODE" == update ]] && ((EUID != 0)); then
    # Re-enter the installed root-owned updater, not an arbitrary user-controlled path.
    ARGS=()
    if [[ -n "$VERSION" ]]; then ARGS+=(--version "$VERSION"); fi
    if ((BASE_ONLY)); then ARGS+=(--base-only); fi
    exec sudo -- /usr/local/sbin/application-update "${ARGS[@]}"
fi
command -v curl >/dev/null || die 'curl is unavailable; rerun the installation bootstrap.'
WORK_DIR=$(mktemp -d /tmp/application-update.XXXXXXXX)
if [[ -n "$VERSION" ]]; then
    URL="https://github.com/$REPOSITORY/releases/download/$VERSION/install.sh"
else
    URL="https://github.com/$REPOSITORY/releases/latest/download/install.sh"
fi
if ! curl --fail --location --silent --show-error --proto '=https' --proto-redir '=https' \
    --tlsv1.2 --retry 3 --connect-timeout 15 --max-time 300 "$URL" -o "$WORK_DIR/install.sh"; then
    die 'Cannot download the public release. Check connectivity, public visibility and publication of install.sh. No token is requested.'
fi
AVAILABLE=$(sed -n "s/^RELEASE_TAG='\([^']*\)'$/\1/p" "$WORK_DIR/install.sh")
[[ "$AVAILABLE" =~ ^v[0-9]+\.[0-9]+\.[0-9]+([.-][A-Za-z0-9.-]+)?$ ]] || die 'Downloaded installer has no valid release tag.'
if [[ -n "$VERSION" && "$AVAILABLE" != "$VERSION" ]]; then die 'Release tag mismatch.'; fi
INSTALLED=none
STATE=unknown
if [[ -r /var/lib/converter/bootstrap-release ]]; then
    { read -r INSTALLED; read -r STATE || true; } < /var/lib/converter/bootstrap-release
fi
printf 'Installed bootstrap: %s (last prerequisite status: %s)\nAvailable: %s\n' "$INSTALLED" "$STATE" "$AVAILABLE"
if [[ "$MODE" == check ]]; then exit 0; fi
if [[ -z "$VERSION" && "$INSTALLED" != none ]] && command -v dpkg >/dev/null; then
    if dpkg --compare-versions "${AVAILABLE#v}" lt "${INSTALLED#v}"; then
        die 'Latest release is older than installed. Select an explicit --version if this downgrade is intended.'
    fi
fi
ARGS=()
if ((BASE_ONLY)); then ARGS+=(--base-only); fi
# Keep this process and its EXIT cleanup alive while the pinned installer runs.
bash "$WORK_DIR/install.sh" "${ARGS[@]}"
