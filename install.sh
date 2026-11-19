#!/usr/bin/env bash
# Public bootstrap: main by default; no Git checkout or credentials on the server.
set -Eeuo pipefail
export LC_ALL=C
umask 022
REPOSITORY=${IP_MEDIA_TOOL_REPOSITORY:-alexbootch/IP_Media_tool}
CHANNEL=main
VERSION=
REVISION=
BASE_ONLY=0
RESTART_RUNNING=0
WORK_DIR=
cleanup() { if [[ -n "$WORK_DIR" && -d "$WORK_DIR" ]]; then rm -rf -- "$WORK_DIR"; fi; }
trap cleanup EXIT
die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }
while (($#)); do
    case "$1" in
        --channel) (($# >= 2)) || die '--channel requires main or stable.'; CHANNEL=$2; shift 2 ;;
        --version) (($# >= 2)) || die '--version requires a tag.'; VERSION=$2; CHANNEL=stable; shift 2 ;;
        --revision) (($# >= 2)) || die '--revision requires a commit SHA.'; REVISION=$2; shift 2 ;;
        --base-only) BASE_ONLY=1; shift ;;
        --restart-running) RESTART_RUNNING=1; shift ;;
        --help|-h)
            printf '%s\n' 'sudo bash install.sh [--channel main|stable] [--version vX.Y.Z] [--base-only]' \
                'Installs web UI, system dependencies, ip-media-tool-admin and ip-media-tool-update.' \
                'Initial service startup is manual; --restart-running restarts an already active service.'
            exit 0 ;;
        *) die "Unknown argument: $1" ;;
    esac
done
if [[ -n "$VERSION" ]]; then CHANNEL=stable; fi
[[ "$(uname -s)" == Linux ]] || die 'Run on the Linux server only.'
((EUID == 0)) || die 'Run with sudo bash install.sh.'
source /etc/os-release
[[ "${ID:-}" == ubuntu && "${VERSION_ID:-}" == 24.04 ]] || die 'Supported profile: Ubuntu 24.04 LTS.'
[[ "$REPOSITORY" =~ ^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$ ]] || die 'Invalid public repository.'
[[ "$CHANNEL" == main || "$CHANNEL" == stable ]] || die 'Channel must be main or stable.'
if [[ -n "$VERSION" ]]; then [[ "$VERSION" =~ ^v[0-9]+\.[0-9]+\.[0-9]+([.-][A-Za-z0-9.-]+)?$ ]] || die 'Invalid tag.'; fi
if [[ -n "$REVISION" ]]; then [[ "$REVISION" =~ ^[0-9a-f]{40}$ ]] || die 'Invalid commit SHA.'; fi
exec 9>/run/lock/ip-media-tool-install.lock
flock -n 9 || die 'Another installation is running.'
MISSING_BOOTSTRAP=()
for package in curl ca-certificates python3; do
    if [[ "$(dpkg-query -W -f='${Status}' "$package" 2>/dev/null || true)" != 'install ok installed' ]]; then MISSING_BOOTSTRAP+=("$package"); fi
done
if ((${#MISSING_BOOTSTRAP[@]})); then
    apt-get update
    apt-get --no-remove --no-install-recommends install -y "${MISSING_BOOTSTRAP[@]}"
fi
WORK_DIR=$(mktemp -d /tmp/ip-media-tool-install.XXXXXXXX)
download() {
    curl --fail --location --silent --show-error --proto '=https' --proto-redir '=https' \
        --tlsv1.2 --retry 3 --connect-timeout 15 --max-time 300 "$1" -o "$2"
}
if [[ -z "$REVISION" ]]; then
    REF=main
    if [[ "$CHANNEL" == stable ]]; then
        if [[ -z "$VERSION" ]]; then
            download "https://api.github.com/repos/$REPOSITORY/releases/latest" "$WORK_DIR/release.json"
            VERSION=$(/usr/bin/python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["tag_name"])' "$WORK_DIR/release.json")
        fi
        [[ "$VERSION" =~ ^v[0-9]+\.[0-9]+\.[0-9]+([.-][A-Za-z0-9.-]+)?$ ]] || die 'Invalid stable tag.'
        REF=$VERSION
    fi
    download "https://api.github.com/repos/$REPOSITORY/commits/$REF" "$WORK_DIR/commit.json"
    REVISION=$(/usr/bin/python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["sha"])' "$WORK_DIR/commit.json")
fi
[[ "$REVISION" =~ ^[0-9a-f]{40}$ ]] || die 'GitHub returned an invalid commit.'
printf 'Installing IP Media Tool: channel=%s commit=%s\n' "$CHANNEL" "$REVISION"
download "https://codeload.github.com/$REPOSITORY/tar.gz/$REVISION" "$WORK_DIR/source.tar.gz"
mkdir "$WORK_DIR/source"
tar -xzf "$WORK_DIR/source.tar.gz" --strip-components=1 --no-same-owner --no-same-permissions -C "$WORK_DIR/source"
SOURCE="$WORK_DIR/source"
for file in src/converter/control/app.py frontend/web/login.html packaging/systemd/ip-media-tool.service tools/install/ip-media-tool-admin.sh tools/install/ip-media-tool-update.sh; do
    [[ -f "$SOURCE/$file" ]] || die "Selected commit does not contain $file. Use a commit/channel with the web application."
done
PREREQUISITE_STATUS=0
# The web control plane can be installed independently of NDI and optional media hardware.
bash "$SOURCE/tools/install/install-components.sh" --yes --base-only || PREREQUISITE_STATUS=$?
((PREREQUISITE_STATUS != 1)) || die 'System dependency installation failed; active application unchanged.'
for package in python3-fastapi python3-uvicorn python3-argon2; do
    [[ "$(dpkg-query -W -f='${Status}' "$package" 2>/dev/null || true)" == 'install ok installed' ]] || die "Web dependency missing: $package"
done
install_command() {
    local temporary
    temporary=$(mktemp /usr/local/sbin/.ip-media-tool-command.XXXXXXXX)
    if ! install -m 0755 "$1" "$temporary" || ! mv -f -- "$temporary" "$2"; then
        rm -f -- "$temporary"
        die "Cannot install $2"
    fi
}
install -d -m 0755 /usr/local/sbin
install_command "$SOURCE/tools/install/ip-media-tool-update.sh" /usr/local/sbin/ip-media-tool-update
if ((BASE_ONLY)); then
    printf 'Base dependency preparation complete. Web application was not installed.\n'
    exit "$PREREQUISITE_STATUS"
fi
if ! id ip-media-tool >/dev/null 2>&1; then
    useradd --system --user-group --home-dir /var/lib/ip-media-tool --no-create-home --shell /usr/sbin/nologin ip-media-tool
fi
[[ "$(id -u ip-media-tool)" != 0 ]] || die 'Service user must not be root.'
install -d -m 0755 /opt/ip-media-tool/releases /var/lib/ip-media-tool /etc/ip-media-tool
install -d -m 0700 -o ip-media-tool -g ip-media-tool /var/lib/ip-media-tool/security
if [[ ! -e /etc/ip-media-tool/environment ]]; then
    cat > /etc/ip-media-tool/environment <<'ENVIRONMENT'
IP_MEDIA_TOOL_BIND=0.0.0.0
IP_MEDIA_TOOL_PORT=8080
IP_MEDIA_TOOL_SECURITY_DIR=/var/lib/ip-media-tool/security
IP_MEDIA_TOOL_COOKIE_SECURE=false
ENVIRONMENT
    chown root:ip-media-tool /etc/ip-media-tool/environment
    chmod 0640 /etc/ip-media-tool/environment
fi
RELEASE_DIR="/opt/ip-media-tool/releases/$REVISION"
if [[ ! -d "$RELEASE_DIR" ]]; then
    STAGING=$(mktemp -d /opt/ip-media-tool/releases/.staging.XXXXXXXX)
    cp -a -- "$SOURCE/." "$STAGING/"
    chmod -R u=rwX,go=rX "$STAGING"
    mv -- "$STAGING" "$RELEASE_DIR"
fi
OLD_CURRENT=$(readlink -f /opt/ip-media-tool/current 2>/dev/null || true)
WAS_RUNNING=0
if systemctl is-active --quiet ip-media-tool.service; then WAS_RUNNING=1; fi
install -m 0644 "$SOURCE/packaging/systemd/ip-media-tool.service" /etc/systemd/system/ip-media-tool.service
install_command "$SOURCE/tools/install/ip-media-tool-admin.sh" /usr/local/sbin/ip-media-tool-admin
LINK_TEMP="/opt/ip-media-tool/.current.$$"
ln -s -- "$RELEASE_DIR" "$LINK_TEMP"
mv -Tf -- "$LINK_TEMP" /opt/ip-media-tool/current
systemctl daemon-reload
if ((RESTART_RUNNING && WAS_RUNNING)); then
    if ! systemctl restart ip-media-tool.service; then
        if [[ -n "$OLD_CURRENT" && -d "$OLD_CURRENT" ]]; then
            ln -s -- "$OLD_CURRENT" "$LINK_TEMP"
            mv -Tf -- "$LINK_TEMP" /opt/ip-media-tool/current
            systemctl restart ip-media-tool.service || true
        fi
        die 'Service restart failed. Previous code pointer was restored when available; inspect journalctl -u ip-media-tool.'
    fi
fi
STATE_TEMP=$(mktemp /var/lib/ip-media-tool/.installed.XXXXXXXX)
printf '%s\n%s\n' "$REVISION" "$CHANNEL" > "$STATE_TEMP"
chmod 0644 "$STATE_TEMP"
mv -f -- "$STATE_TEMP" /var/lib/ip-media-tool/installed-revision
printf '\nIP Media Tool web application installed.\n'
printf '%s\n' 'First setup: ip-media-tool-admin bootstrap' \
    'Manual start: sudo systemctl start ip-media-tool' \
    'Browser: http://SERVER-IP:8080' \
    'Updates: ip-media-tool-update (main by default; restarts only an already running web service)'
if ((PREREQUISITE_STATUS != 0)); then
    printf 'Media prerequisites have unresolved checks (%s); web installation completed independently.\n' "$PREREQUISITE_STATUS" >&2
fi
