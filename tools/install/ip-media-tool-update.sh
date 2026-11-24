#!/usr/bin/env bash
# Public main-channel updater; the Windows developer controls push, the operator controls execution.
set -Eeuo pipefail
export LC_ALL=C
REPOSITORY=${IP_MEDIA_TOOL_REPOSITORY:-alexbootch/IP_Media_tool}
CHANNEL=main
REQUESTED_TAG=
MODE=update
BASE_ONLY=0
WORK_DIR=
die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }
cleanup() { if [[ -n "$WORK_DIR" && -d "$WORK_DIR" ]]; then rm -rf -- "$WORK_DIR"; fi; }
trap cleanup EXIT
while (($#)); do
    case "$1" in
        --check) MODE=check; shift ;;
        --channel) (($# >= 2)) || die '--channel requires main or stable.'; CHANNEL=$2; shift 2 ;;
        --version) (($# >= 2)) || die '--version requires a tag.'; REQUESTED_TAG=$2; CHANNEL=stable; shift 2 ;;
        --base-only) BASE_ONLY=1; shift ;;
        --help|-h)
            printf '%s\n' 'ip-media-tool-update [--check] [--channel main|stable] [--version vX.Y.Z] [--base-only]' \
                'Default: current main commit. No Git login or release publication required.' \
                'Application updates stop and restore active services. Running media routes will stop.'
            exit 0 ;;
        *) die "Unknown argument: $1" ;;
    esac
done
if [[ -n "$REQUESTED_TAG" ]]; then CHANNEL=stable; fi
[[ "$REPOSITORY" =~ ^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$ ]] || die 'Invalid public repository.'
[[ "$CHANNEL" == main || "$CHANNEL" == stable ]] || die 'Channel must be main or stable.'
if [[ -n "$REQUESTED_TAG" ]]; then [[ "$REQUESTED_TAG" =~ ^v[0-9]+\.[0-9]+\.[0-9]+([.-][A-Za-z0-9.-]+)?$ ]] || die 'Invalid release tag; expected vX.Y.Z.'; fi
if [[ "$MODE" == update ]] && ((EUID != 0)); then
    ARGS=(--channel "$CHANNEL")
    if [[ -n "$REQUESTED_TAG" ]]; then ARGS+=(--version "$REQUESTED_TAG"); fi
    if ((BASE_ONLY)); then ARGS+=(--base-only); fi
    exec sudo -- /usr/local/sbin/ip-media-tool-update "${ARGS[@]}"
fi
command -v curl >/dev/null || die 'curl is missing. Rerun the public installer.'
command -v /usr/bin/python3 >/dev/null || die 'System Python is missing. Rerun the public installer.'
WORK_DIR=$(mktemp -d /tmp/ip-media-tool-update.XXXXXXXX)
download() {
    curl --fail --location --silent --show-error --proto '=https' --proto-redir '=https' \
        --tlsv1.2 --retry 3 --connect-timeout 15 --max-time 300 "$1" -o "$2"
}
REF=main
if [[ "$CHANNEL" == stable ]]; then
    if [[ -z "$REQUESTED_TAG" ]]; then
        download "https://api.github.com/repos/$REPOSITORY/releases/latest" "$WORK_DIR/release.json"
        REQUESTED_TAG=$(/usr/bin/python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["tag_name"])' "$WORK_DIR/release.json")
    fi
    [[ "$REQUESTED_TAG" =~ ^v[0-9]+\.[0-9]+\.[0-9]+([.-][A-Za-z0-9.-]+)?$ ]] || die 'Invalid stable release tag.'
    REF=$REQUESTED_TAG
fi
download "https://api.github.com/repos/$REPOSITORY/commits/$REF" "$WORK_DIR/commit.json"
REVISION=$(/usr/bin/python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["sha"])' "$WORK_DIR/commit.json")
[[ "$REVISION" =~ ^[0-9a-f]{40}$ ]] || die 'Invalid commit SHA returned by GitHub.'
INSTALLED=none
if [[ -r /var/lib/ip-media-tool/installed-revision ]]; then read -r INSTALLED < /var/lib/ip-media-tool/installed-revision; fi
printf 'Installed: %s\nAvailable %s: %s\n' "$INSTALLED" "$CHANNEL" "$REVISION"
if [[ "$MODE" == check ]]; then exit 0; fi
download "https://raw.githubusercontent.com/$REPOSITORY/$REVISION/install.sh" "$WORK_DIR/install.sh"
ARGS=(--channel "$CHANNEL" --revision "$REVISION" --restart-running)
if [[ -n "$REQUESTED_TAG" ]]; then ARGS+=(--version "$REQUESTED_TAG"); fi
if ((BASE_ONLY)); then ARGS+=(--base-only); fi
bash "$WORK_DIR/install.sh" "${ARGS[@]}"
