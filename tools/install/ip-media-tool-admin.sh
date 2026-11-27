#!/usr/bin/env bash
set -euo pipefail
umask 077
if ((EUID != 0)); then exec sudo -- /usr/local/sbin/ip-media-tool-admin "$@"; fi
if [[ -f /etc/ip-media-tool/environment ]]; then
    set -a
    source /etc/ip-media-tool/environment
    set +a
fi
exec runuser -u ip-media-tool -- env \
    PYTHONPATH=/opt/ip-media-tool/current/src \
    IP_MEDIA_TOOL_LOG_DIR=/opt/ip-media-tool/logs \
    IP_MEDIA_TOOL_SECURITY_DIR="${IP_MEDIA_TOOL_SECURITY_DIR:-/var/lib/ip-media-tool/security}" \
    /usr/bin/python3 -m converter.control.admin "$@"
