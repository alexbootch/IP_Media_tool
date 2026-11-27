#!/usr/bin/env bash
# Public bootstrap: main by default; no Git checkout or credentials on the server.
set -Eeuo pipefail
export LC_ALL=C
umask 022
REPOSITORY=${IP_MEDIA_TOOL_REPOSITORY:-alexbootch/IP_Media_tool}
CHANNEL=main
REQUESTED_TAG=
REVISION=
BASE_ONLY=0
RESTART_RUNNING=0
WORK_DIR=
LINK_TEMP=
STATE_TEMP=
ROLLBACK=0
FILES_CHANGED=0
POINTER_SWITCHED=0
STAGING=
CREATED_RELEASE=
INSTALL_COMMITTED=0
ACTIVE_UNITS=()
MANAGED_FILES=(
    /etc/systemd/system/ip-media-tool.service
    /etc/systemd/system/ip-media-tool-runtime.service
    /etc/logrotate.d/ip-media-tool
    /usr/local/sbin/ip-media-tool-admin
    /usr/local/sbin/ip-media-tool-update
    /var/lib/ip-media-tool/installed-revision
)
CURRENT=/opt/ip-media-tool/current
RELEASE_ROOT=/opt/ip-media-tool/releases
LOG_DIR=/opt/ip-media-tool/logs
LOG_GROUP=ip-media-tool-logs
OLD_LINK=

remove_inactive_release() {
    local candidate=$1 name resolved active mounts target
    name=${candidate##*/}
    [[ "$name" =~ ^[0-9a-f]{40}$ && "$candidate" == "$RELEASE_ROOT/$name" ]] || return 1
    [[ -d "$candidate" && ! -L "$candidate" ]] || return 1
    resolved=$(readlink -e -- "$candidate") || return 1
    active=$(readlink -e -- "$CURRENT") || active=
    [[ "$resolved" == "$candidate" && "$candidate" != "$active" ]] || return 1
    [[ "$(stat -c '%u' -- "$candidate")" == 0 ]] || return 1
    [[ -f "$candidate/src/converter/control/app.py" && -f "$candidate/packaging/systemd/ip-media-tool.service" ]] || return 1
    mounts=$(findmnt -ln -o TARGET) || return 1
    while IFS= read -r target; do
        [[ "$target" != "$candidate" && "$target" != "$candidate/"* ]] || return 1
    done <<< "$mounts"
    rm -rf --one-file-system -- "$candidate" && [[ ! -e "$candidate" ]]
}

prune_releases() {
    local candidate
    [[ "$(readlink -e -- "$CURRENT")" == "$RELEASE_DIR" ]] || return 1
    for candidate in "$RELEASE_ROOT"/*; do
        [[ "${candidate##*/}" =~ ^[0-9a-f]{40}$ && "$candidate" != "$RELEASE_DIR" ]] || continue
        if remove_inactive_release "$candidate"; then
            printf 'Removed inactive release: %s\n' "${candidate##*/}"
        else
            printf 'WARNING: could not remove inactive release: %s\n' "$candidate" >&2
        fi
    done
}

wait_for_service() {
    local unit=$1 deadline=$((SECONDS + 25)) pid setting host port socket response
    while ((SECONDS < deadline)); do
        if systemctl is-active --quiet "$unit"; then
            pid=$(systemctl show --property=MainPID --value "$unit") || pid=
            if [[ "$pid" =~ ^[1-9][0-9]*$ && -r "/proc/$pid/environ" ]]; then
                host=0.0.0.0 port=8080 socket=/run/ip-media-tool/runtime.sock
                while IFS= read -r -d '' setting; do
                    case "$setting" in
                        IP_MEDIA_TOOL_BIND=*) host=${setting#*=} ;;
                        IP_MEDIA_TOOL_PORT=*) port=${setting#*=} ;;
                        IP_MEDIA_TOOL_RUNTIME_SOCKET=*) socket=${setting#*=} ;;
                    esac
                done < "/proc/$pid/environ"
                if [[ "$unit" == ip-media-tool-runtime.service ]]; then
                    if [[ -S "$socket" ]]; then return 0; fi
                else
                    case "$host" in
                        0.0.0.0) host=127.0.0.1 ;;
                        ::) host='[::1]' ;;
                        *:*) host="[$host]" ;;
                    esac
                    if response=$(curl -fSs --noproxy '*' --max-time 2 "http://$host:$port/healthz" 2>/dev/null) &&
                       [[ "$response" == *'"application":"IP_Media_tool"'* ]]; then return 0; fi
                fi
            fi
        fi
        sleep 1
    done
    return 1
}

cleanup() {
    local status=$? path saved recovery_failed=0
    trap - EXIT
    set +e
    if ((ROLLBACK)); then
        printf 'Update failed. Restoring the previous version and service state.\n' >&2
        if ((POINTER_SWITCHED)); then
            if ((${#ACTIVE_UNITS[@]})); then
                systemctl stop "${ACTIVE_UNITS[@]}" || recovery_failed=1
            fi
            if [[ -n "$OLD_LINK" ]]; then
                ln -s -- "$OLD_LINK" "$LINK_TEMP.rollback" && mv -Tf -- "$LINK_TEMP.rollback" "$CURRENT" || recovery_failed=1
            else
                rm -f -- "$CURRENT" || recovery_failed=1
            fi
        fi
        if ((FILES_CHANGED)); then
            for path in "${MANAGED_FILES[@]}"; do
                saved="$WORK_DIR/previous/${path##*/}"
                if [[ -e "$saved" || -L "$saved" ]]; then
                    cp -aT --remove-destination -- "$saved" "$path" || recovery_failed=1
                else
                    rm -f -- "$path" || recovery_failed=1
                fi
            done
            systemctl daemon-reload || recovery_failed=1
        fi
        if (( ! recovery_failed )); then
            for path in "${ACTIVE_UNITS[@]}"; do
                systemctl start "$path" || recovery_failed=1
            done
        fi
        if ((recovery_failed)); then
            printf 'Automatic recovery was incomplete. Check ip-media-tool and ip-media-tool-runtime in systemctl.\n' >&2
        fi
    fi
    if (( ! INSTALL_COMMITTED && ! recovery_failed )) && [[ -n "$CREATED_RELEASE" ]]; then
        remove_inactive_release "$CREATED_RELEASE" || true
    fi
    if [[ -n "$STAGING" && ! -L "$STAGING" && "$STAGING" == "$RELEASE_ROOT"/.staging.* ]] &&
       [[ "$(readlink -e -- "$STAGING")" == "$STAGING" ]]; then
        rm -rf --one-file-system -- "$STAGING"
    fi
    if [[ -n "$LINK_TEMP" ]]; then rm -f -- "$LINK_TEMP" "$LINK_TEMP.rollback"; fi
    if [[ -n "$STATE_TEMP" ]]; then rm -f -- "$STATE_TEMP"; fi
    if [[ -n "$WORK_DIR" && -d "$WORK_DIR" ]]; then rm -rf -- "$WORK_DIR"; fi
    exit "$status"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }
while (($#)); do
    case "$1" in
        --channel) (($# >= 2)) || die '--channel requires main or stable.'; CHANNEL=$2; shift 2 ;;
        --version) (($# >= 2)) || die '--version requires a tag.'; REQUESTED_TAG=$2; CHANNEL=stable; shift 2 ;;
        --revision) (($# >= 2)) || die '--revision requires a commit SHA.'; REVISION=$2; shift 2 ;;
        --base-only) BASE_ONLY=1; shift ;;
        --restart-running) RESTART_RUNNING=1; shift ;;
        --help|-h)
            printf '%s\n' 'sudo bash install.sh [--channel main|stable] [--version vX.Y.Z] [--base-only]' \
                'Installs web UI, system dependencies, ip-media-tool-admin and ip-media-tool-update.' \
                'Initial service startup is manual; --restart-running stops and restores active application services.'
            exit 0 ;;
        *) die "Unknown argument: $1" ;;
    esac
done
if [[ -n "$REQUESTED_TAG" ]]; then CHANNEL=stable; fi
[[ "$(uname -s)" == Linux ]] || die 'Run on the Linux server only.'
((EUID == 0)) || die 'Run with sudo bash install.sh.'
# Keep OS variables (notably VERSION) out of the installer argument namespace.
OS_PROFILE=$(
    source /etc/os-release
    printf '%s:%s' "${ID:-}" "${VERSION_ID:-}"
)
[[ "$OS_PROFILE" == ubuntu:24.04 ]] || die 'Supported profile: Ubuntu 24.04 LTS.'
[[ "$REPOSITORY" =~ ^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$ ]] || die 'Invalid public repository.'
[[ "$CHANNEL" == main || "$CHANNEL" == stable ]] || die 'Channel must be main or stable.'
if [[ -n "$REQUESTED_TAG" ]]; then [[ "$REQUESTED_TAG" =~ ^v[0-9]+\.[0-9]+\.[0-9]+([.-][A-Za-z0-9.-]+)?$ ]] || die 'Invalid release tag; expected vX.Y.Z.'; fi
if [[ -n "$REVISION" ]]; then [[ "$REVISION" =~ ^[0-9a-f]{40}$ ]] || die 'Invalid commit SHA.'; fi
exec 9>/run/lock/ip-media-tool-install.lock
flock -n 9 || die 'Another installation is running.'
if ((!BASE_ONLY)); then
    [[ ! -L /opt/ip-media-tool && ! -L "$LOG_DIR" ]] || die 'Expected real directories for installed application logs.'
    install -d -m 0750 "$LOG_DIR"
    [[ ! -L "$LOG_DIR/install.log" ]] || die 'Installation log must not be a symlink.'
    if [[ ! -e "$LOG_DIR/install.log" ]]; then
        install -m 0600 /dev/null "$LOG_DIR/install.log"
    fi
    [[ -f "$LOG_DIR/install.log" ]] || die 'Installation log must be a regular file.'
    exec > >(tee -a "$LOG_DIR/install.log") 2>&1
    printf '\nInstallation started at %s\n' "$(date -Is)"
fi
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
        if [[ -z "$REQUESTED_TAG" ]]; then
            download "https://api.github.com/repos/$REPOSITORY/releases/latest" "$WORK_DIR/release.json"
            REQUESTED_TAG=$(/usr/bin/python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["tag_name"])' "$WORK_DIR/release.json")
        fi
        [[ "$REQUESTED_TAG" =~ ^v[0-9]+\.[0-9]+\.[0-9]+([.-][A-Za-z0-9.-]+)?$ ]] || die 'Invalid stable tag.'
        REF=$REQUESTED_TAG
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
for file in src/converter/control/app.py frontend/web/login.html packaging/systemd/ip-media-tool.service packaging/systemd/ip-media-tool-runtime.service packaging/logrotate/ip-media-tool tools/install/ip-media-tool-admin.sh tools/install/ip-media-tool-update.sh; do
    [[ -f "$SOURCE/$file" ]] || die "Selected commit does not contain $file. Use a commit/channel with the web application."
done
PREREQUISITE_STATUS=0
# The web control plane can be installed independently of NDI and optional media hardware.
bash "$SOURCE/tools/install/install-components.sh" --yes --base-only || PREREQUISITE_STATUS=$?
((PREREQUISITE_STATUS != 1)) || die 'System dependency installation failed; active application unchanged.'
for package in python3-fastapi python3-uvicorn python3-argon2 python3-jsonschema; do
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
if ((BASE_ONLY)); then
    install_command "$SOURCE/tools/install/ip-media-tool-update.sh" /usr/local/sbin/ip-media-tool-update
    printf 'Base dependency preparation complete. Web application was not installed.\n'
    exit "$PREREQUISITE_STATUS"
fi
if ! id ip-media-tool >/dev/null 2>&1; then
    useradd --system --user-group --home-dir /var/lib/ip-media-tool --no-create-home --shell /usr/sbin/nologin ip-media-tool
fi
[[ "$(id -u ip-media-tool)" != 0 ]] || die 'Service user must not be root.'
[[ ! -L "$RELEASE_ROOT" ]] || die 'Release storage must be a real directory.'
install -d -m 0755 "$RELEASE_ROOT" /var/lib/ip-media-tool /etc/ip-media-tool
[[ "$(readlink -e -- "$RELEASE_ROOT")" == "$RELEASE_ROOT" && "$(stat -c '%u' -- "$RELEASE_ROOT")" == 0 ]] ||
    die 'Release storage must be owned by root at /opt/ip-media-tool/releases.'
install -d -m 0700 -o ip-media-tool -g ip-media-tool /var/lib/ip-media-tool/security
install -d -m 0700 -o ip-media-tool -g ip-media-tool /var/lib/ip-media-tool/hardware
install -d -m 0700 -o ip-media-tool -g ip-media-tool /etc/ip-media-tool/nodes
install -d -m 0750 -o root -g ip-media-tool "$LOG_DIR"
getent group "$LOG_GROUP" >/dev/null || groupadd --system -- "$LOG_GROUP"
setfacl -m "g:$LOG_GROUP:rx" -- "$LOG_DIR"
for log_subdir in nodes audit; do
    [[ ! -L "$LOG_DIR/$log_subdir" ]] || die 'Application log subdirectory must not be a symlink.'
    install -d -m 0700 -o ip-media-tool -g ip-media-tool "$LOG_DIR/$log_subdir"
    setfacl -m "g:$LOG_GROUP:rx,d:g:$LOG_GROUP:rx" -- "$LOG_DIR/$log_subdir"
done
for service_log in runtime.log web.log; do
    [[ ! -L "$LOG_DIR/$service_log" ]] || die 'Service log must not be a symlink.'
    if [[ ! -e "$LOG_DIR/$service_log" ]]; then
        install -m 0660 -o root -g ip-media-tool /dev/null "$LOG_DIR/$service_log"
    fi
    [[ -f "$LOG_DIR/$service_log" ]] || die 'Service log must be a regular file.'
done
find -P "$LOG_DIR" -maxdepth 1 -type f -exec setfacl -m "g:$LOG_GROUP:r" -- {} +
# The caller's working directory can be a private home that the service user cannot enter.
(
    cd /
    runuser -u ip-media-tool -- find -P "$LOG_DIR/nodes" "$LOG_DIR/audit" -maxdepth 1 -type f \
        -exec setfacl -m "g:$LOG_GROUP:r" -- {} +
)
if [[ -n "${SUDO_USER:-}" && "${SUDO_USER}" != root && "${SUDO_USER}" != ip-media-tool &&
      "${SUDO_UID:-}" =~ ^[1-9][0-9]*$ ]]; then
    operator_uid=$(id -u -- "$SUDO_USER" 2>/dev/null) || operator_uid=
    if [[ "$operator_uid" == "$SUDO_UID" && " $(id -nG -- "$SUDO_USER") " != *" $LOG_GROUP "* ]]; then
        usermod -a -G "$LOG_GROUP" -- "$SUDO_USER" ||
            printf 'WARNING: could not grant log access to %s. Check group %s.\n' "$SUDO_USER" "$LOG_GROUP" >&2
    fi
fi
# Supplementary device access without creating absent groups or changing driver/device permissions.
for device_group in video render; do
    if getent group "$device_group" >/dev/null; then usermod -a -G "$device_group" ip-media-tool; fi
done
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
RELEASE_DIR="$RELEASE_ROOT/$REVISION"
[[ ! -L "$RELEASE_DIR" ]] || die 'Selected release directory must not be a symlink.'
if [[ ! -d "$RELEASE_DIR" ]]; then
    STAGING=$(mktemp -d "$RELEASE_ROOT/.staging.XXXXXXXX")
    cp -a -- "$SOURCE/." "$STAGING/"
    chmod -R u=rwX,go=rX "$STAGING"
    mv -T -- "$STAGING" "$RELEASE_DIR"
    CREATED_RELEASE=$RELEASE_DIR STAGING=
fi
if [[ -L "$CURRENT" ]]; then
    OLD_LINK=$(readlink -- "$CURRENT")
elif [[ -e "$CURRENT" ]]; then
    die "Expected a release symlink at $CURRENT."
fi
for unit in ip-media-tool-runtime.service ip-media-tool.service; do
    if systemctl is-active --quiet "$unit"; then ACTIVE_UNITS+=("$unit"); fi
done
if ((${#ACTIVE_UNITS[@]} && ! RESTART_RUNNING)); then
    die 'Application services are active. Use ip-media-tool-update to stop and restore them.'
fi
mkdir "$WORK_DIR/previous"
for path in "${MANAGED_FILES[@]}"; do
    if [[ -e "$path" || -L "$path" ]]; then cp -a -- "$path" "$WORK_DIR/previous/"; fi
done
LINK_TEMP="/opt/ip-media-tool/.current.$$"
STATE_TEMP=$(mktemp /var/lib/ip-media-tool/.installed.XXXXXXXX)
printf '%s\n%s\n' "$REVISION" "$CHANNEL" > "$STATE_TEMP"
chmod 0644 "$STATE_TEMP"
ROLLBACK=1
# Stop the web API before runtime so it cannot submit new worker commands during activation.
for ((index=${#ACTIVE_UNITS[@]}-1; index>=0; index--)); do
    printf 'Stopping %s\n' "${ACTIVE_UNITS[index]}"
    systemctl stop "${ACTIVE_UNITS[index]}" || die "Could not stop ${ACTIVE_UNITS[index]}."
done
FILES_CHANGED=1
install -m 0644 "$SOURCE/packaging/systemd/ip-media-tool.service" /etc/systemd/system/ip-media-tool.service
install -m 0644 "$SOURCE/packaging/systemd/ip-media-tool-runtime.service" /etc/systemd/system/ip-media-tool-runtime.service
install -m 0644 "$SOURCE/packaging/logrotate/ip-media-tool" /etc/logrotate.d/ip-media-tool
install_command "$SOURCE/tools/install/ip-media-tool-admin.sh" /usr/local/sbin/ip-media-tool-admin
install_command "$SOURCE/tools/install/ip-media-tool-update.sh" /usr/local/sbin/ip-media-tool-update
ln -s -- "$RELEASE_DIR" "$LINK_TEMP"
POINTER_SWITCHED=1
mv -Tf -- "$LINK_TEMP" "$CURRENT"
systemctl daemon-reload
for unit in "${ACTIVE_UNITS[@]}"; do
    printf 'Starting %s\n' "$unit"
    systemctl start "$unit" || die "Could not start $unit."
done
for unit in "${ACTIVE_UNITS[@]}"; do
    wait_for_service "$unit" || die "$unit did not become ready."
done
mv -f -- "$STATE_TEMP" /var/lib/ip-media-tool/installed-revision
ROLLBACK=0 INSTALL_COMMITTED=1
prune_releases || printf 'WARNING: release cleanup skipped because current changed.\n' >&2
printf '\nIP Media Tool web application installed.\n'
printf '%s\n' 'First setup: ip-media-tool-admin bootstrap' \
    'Manual start: sudo systemctl start ip-media-tool' \
    'Manual worker runtime: sudo systemctl start ip-media-tool-runtime (does not start nodes)' \
    'Browser: http://SERVER-IP:8080' \
    'Logs: /opt/ip-media-tool/logs/' \
    'Updates: ip-media-tool-update (main by default; restores previously active services, without starting media routes)'
if ((PREREQUISITE_STATUS != 0)); then
    printf 'Media prerequisites have unresolved checks (%s); web installation completed independently.\n' "$PREREQUISITE_STATUS" >&2
fi
