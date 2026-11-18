#!/usr/bin/env bash
# Run manually on Ubuntu, never on the Windows development workstation.
set -Eeuo pipefail
export LC_ALL=C

MODE=install
ASSUME_YES=0
BASE_ONLY=0
ISSUES=0
TEMP_DIR=
SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
PROJECT_DIR=$(cd -- "$SCRIPT_DIR/../.." && pwd)
PACKAGE_FILE="$PROJECT_DIR/packaging/ubuntu-24.04.packages"
DECKLINK_BASELINE=16.4a1
NVIDIA_BASELINE=580.178.04
PACKAGES=()
MISSING=()

log() { printf '%s\n' "$*"; }
warn() { printf 'WARNING: %s\n' "$*" >&2; }
die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }
issue() { warn "$*"; ISSUES=$((ISSUES + 1)); }
cleanup() { if [[ -n "$TEMP_DIR" && -d "$TEMP_DIR" ]]; then rm -rf -- "$TEMP_DIR"; fi; }
trap cleanup EXIT
trap 'printf "ERROR: command failed at line %s (exit %s). No automatic rollback of OS packages was attempted.\n" "$LINENO" "$?" >&2' ERR

usage() {
    cat <<'USAGE'
Usage: sudo bash tools/install/install-components.sh [--yes] [--base-only]
       bash tools/install/install-components.sh --check [--base-only]

--check      Inspect installed prerequisites; do not install packages or update APT.
--yes        Accept the displayed APT installation transaction without its prompt.
--base-only  Prepare the distribution runtime; missing NDI does not affect exit status.
--help       Show this help.

Exit codes: 0 = selected scope satisfied; 1 = installation/command error;
            2 = unresolved prerequisites (including NDI unless --base-only).

No drivers, application code, Python pip packages or SDKs are downloaded/built.
No Converter services, media pipelines, tests, reboots or Git operations run.
Distribution package maintainer scripts may start their own services (e.g. Avahi).
USAGE
}

for arg in "$@"; do
    case "$arg" in
        --check) MODE=check ;;
        --yes) ASSUME_YES=1 ;;
        --base-only) BASE_ONLY=1 ;;
        --help|-h) usage; exit 0 ;;
        *) die "Unknown argument: $arg (use --help)" ;;
    esac
done
[[ "$(uname -s)" == Linux ]] || die 'Run this script on the Linux server, not Windows.'
[[ -r /etc/os-release ]] || die '/etc/os-release is unavailable.'
# OS-owned file, not a repository-supplied script.
source /etc/os-release
[[ "${ID:-}" == ubuntu && "${VERSION_ID:-}" == 24.04 ]] ||
    die "Supported installer profile: Ubuntu 24.04 LTS. Detected: ${PRETTY_NAME:-unknown}."
for tool in dpkg-query dpkg awk grep readlink mktemp timeout; do
    command -v "$tool" >/dev/null || die "Required base OS command is missing: $tool"
done
[[ -r "$PACKAGE_FILE" ]] || die "Missing package list: $PACKAGE_FILE. Use the complete checkout."
while IFS= read -r package || [[ -n "$package" ]]; do
    package=${package%%#*}
    [[ -n "$package" ]] || continue
    [[ "$package" =~ ^[a-z0-9][a-z0-9.+-]*$ ]] || die "Invalid package entry: $package"
    PACKAGES+=("$package")
done < "$PACKAGE_FILE"
((${#PACKAGES[@]} > 0)) || die 'Empty package list.'
if [[ "$MODE" == install ]]; then
    ((EUID == 0)) || die 'Installation requires root: sudo bash tools/install/install-components.sh'
    command -v apt-get >/dev/null || die 'apt-get is missing.'
    command -v flock >/dev/null || die 'flock is missing (base Ubuntu util-linux package).'
    # No concurrent Converter installers; APT also maintains its own package lock.
    exec 9>/run/lock/converter-components.lock
    flock -n 9 || die 'Another Converter component installation is in progress.'
fi

log "Converter prerequisites: $PRETTY_NAME / $(dpkg --print-architecture) / kernel $(uname -r)"
log "Mode: $MODE; recorded driver baseline: Desktop Video $DECKLINK_BASELINE, NVIDIA $NVIDIA_BASELINE"

installed() {
    local status
    status=$(dpkg-query -W -f='${Status}' "$1" 2>/dev/null) || return 1
    [[ "$status" == 'install ok installed' ]]
}

check_driver() {
    local vendor=$1 label=$2 module=$3 expected=$4 found=0 bound=0 device value driver version
    for device in /sys/bus/pci/devices/*; do
        [[ -r "$device/vendor" ]] || continue
        read -r value < "$device/vendor"
        [[ "$value" == "$vendor" ]] || continue
        if [[ "$vendor" == 0x10de ]]; then
            read -r value < "$device/class"
            [[ "$value" == 0x03* ]] || continue
        fi
        found=$((found + 1))
        driver=$(readlink -- "$device/driver" 2>/dev/null) || driver=
        driver=${driver##*/}
        log "$label PCI ${device##*/}: driver=${driver:-unbound}"
        if [[ "$module" == blackmagic_io && "$driver" == blackmagic-io ]] ||
           [[ "$driver" == "$module" ]]; then
            bound=$((bound + 1))
        fi
    done
    if ((found == 0)); then
        log "$label: no matching PCI hardware found; this driver is not required on this host."
        return
    fi
    if ((bound != found)) || [[ ! -d "/sys/module/$module" ]]; then
        issue "$label: driver missing, unloaded, or not bound to every detected device."
        if [[ "$module" == blackmagic_io ]]; then
            log "ACTION: manually install Blackmagic Desktop Video for Linux compatible with your card/kernel (baseline $expected), complete any required reboot, then rerun this script."
            log 'Download: https://www.blackmagicdesign.com/support/'
        else
            log "ACTION: manually install an NVIDIA Linux driver compatible with your GPU/kernel, including encode/decode userspace libraries (baseline $expected); complete any required reboot and confirm nvidia-smi."
            log 'Download: https://www.nvidia.com/Download/index.aspx'
        fi
        return
    fi
    version=
    if [[ -r "/sys/module/$module/version" ]]; then read -r version < "/sys/module/$module/version"; fi
    log "$label: loaded version=${version:-unknown}; recorded baseline=$expected"
    if [[ -n "$version" && "$version" != "$expected" ]]; then
        warn "$label version differs from the inventory baseline; no automatic driver replacement. Media compatibility remains to be verified."
    fi
    if [[ "$module" == nvidia ]]; then
        if ! command -v nvidia-smi >/dev/null || ! timeout 15s nvidia-smi --query-gpu=name,driver_version --format=csv,noheader; then
            issue 'NVIDIA: nvidia-smi is unavailable or unsuccessful. Repair the driver/userspace installation manually.'
        fi
        if command -v ldconfig >/dev/null; then
            local library_cache library
            library_cache=$(ldconfig -p 2>/dev/null) || library_cache=
            for library in libcuda.so.1 libnvidia-encode.so.1 libnvcuvid.so.1; do
                if ! awk -v name="$library" '$1 == name {found=1} END {exit !found}' <<< "$library_cache"; then
                    issue "NVIDIA driver library missing from linker cache: $library. Install matching NVIDIA driver encode/decode userspace libraries manually."
                fi
            done
        else
            issue 'ldconfig is unavailable; NVIDIA userspace library availability could not be checked.'
        fi
    fi
}

log '=== Driver checks (drivers will not be installed or replaced) ==='
check_driver 0xbdbd DeckLink blackmagic_io "$DECKLINK_BASELINE"
check_driver 0x10de NVIDIA nvidia "$NVIDIA_BASELINE"

log '=== Distribution components ==='
for package in "${PACKAGES[@]}"; do
    if installed "$package"; then
        dpkg-query -W -f='OK ${binary:Package} ${Version}\n' "$package"
    else
        log "MISSING $package"
        MISSING+=("$package")
    fi
done

if [[ "$MODE" == install && ${#MISSING[@]} -gt 0 ]]; then
    log 'Refreshing APT indexes; no repositories or PPAs will be added.'
    apt-get update
    # Refuse removals and any driver/kernel/container changes before installation.
    TEMP_DIR=$(mktemp -d /tmp/converter-components.XXXXXXXX)
    apt-get --simulate --no-remove --no-upgrade --no-install-recommends install "${MISSING[@]}" > "$TEMP_DIR/apt-plan.txt"
    cat "$TEMP_DIR/apt-plan.txt"
    if awk '/^(Inst|Remv) / {print $2}' "$TEMP_DIR/apt-plan.txt" |
        grep -Eq '^(desktopvideo|blackmagic|nvidia-|libnvidia-|linux-|kube|docker|containerd)'; then
        die 'APT proposes changing protected driver/kernel/container packages. Resolve dependencies manually; no installation transaction started.'
    fi
    APT_ARGS=(--no-remove --no-upgrade --no-install-recommends)
    if ((ASSUME_YES)); then APT_ARGS+=(-y); fi
    apt-get "${APT_ARGS[@]}" install "${MISSING[@]}"
fi

for package in "${PACKAGES[@]}"; do
    if ! installed "$package"; then issue "Required distribution package is still missing: $package"; fi
done

log '=== GStreamer factory availability (no media pipelines) ==='
if command -v gst-inspect-1.0 >/dev/null; then
    # Keep registry inspection out of the service/user caches, even in --check mode.
    if [[ -z "$TEMP_DIR" ]]; then TEMP_DIR=$(mktemp -d /tmp/converter-components.XXXXXXXX); fi
    export GST_REGISTRY="$TEMP_DIR/gst-registry.bin"
    gst-inspect-1.0 --version
    FACTORIES=(shmsrc shmsink videoconvert videoscale videorate deinterlace interlace
        audioconvert audioresample srtsrc srtsink tsdemux mpegtsmux
        webrtcbin nicesrc nicesink udpsrc rtph264pay rtpopuspay opusenc x264enc
        decklinkvideosrc decklinkaudiosrc decklinkvideosink decklinkaudiosink)
    for factory in "${FACTORIES[@]}"; do
        if timeout 30s gst-inspect-1.0 "$factory" > "$TEMP_DIR/inspect.txt" 2>&1; then
            log "OK GStreamer $factory"
        else
            issue "GStreamer factory/plugin unavailable: $factory"
            cat "$TEMP_DIR/inspect.txt" >&2
        fi
    done
    if [[ -d /sys/module/nvidia ]]; then
        for factory in nvh264enc nvh265enc; do
            if timeout 30s gst-inspect-1.0 "$factory" > "$TEMP_DIR/inspect.txt" 2>&1; then
                log "AVAILABLE GStreamer $factory (per-device capabilities not validated)"
            else
                warn "Optional GPU encoder $factory unavailable; CPU encoding remains possible. No plugin build or driver update will run."
            fi
        done
    fi
    log '=== System Python / GI / GStreamer / GstWebRTC ==='
    if timeout 30s /usr/bin/python3 - > "$TEMP_DIR/python-gi.txt" 2>&1 <<'PYTHON'
import sys
import traceback

stage = "import gi"
try:
    print("Python:", sys.executable, sys.version.split()[0], flush=True)
    import gi

    stage = "load Gst 1.0 typelib and Python overrides"
    gi.require_version("Gst", "1.0")
    from gi.repository import Gst

    stage = "initialize GStreamer (no pipeline)"
    Gst.init(None)
    print("OK GStreamer:", Gst.version_string(), flush=True)

    stage = "load GstWebRTC 1.0 typelib"
    gi.require_version("GstWebRTC", "1.0")
    from gi.repository import GstWebRTC

    stage = "resolve GstWebRTC session description type"
    GstWebRTC.WebRTCSessionDescription
    print("OK Python GI / Gst / GstWebRTC", flush=True)
except Exception:
    print("FAILED stage:", stage, file=sys.stderr, flush=True)
    traceback.print_exc()
    sys.exit(1)
PYTHON
    then
        cat "$TEMP_DIR/python-gi.txt"
    else
        issue 'System Python GI/GStreamer verification failed or timed out; detailed output follows.'
        cat "$TEMP_DIR/python-gi.txt" >&2
        log 'ACTION: inspect the failing stage above. Relevant packages: python3-gi, python3-gst-1.0, gir1.2-gstreamer-1.0, gir1.2-gst-plugins-bad-1.0. Do not replace system GI with a pip install.'
    fi
    NDI_READY=1
    for factory in ndisrc ndisrcdemux ndisinkcombiner ndisink; do
        if ! timeout 30s gst-inspect-1.0 "$factory" > "$TEMP_DIR/ndi-inspect.txt" 2>&1; then NDI_READY=0; fi
    done
else
    issue 'gst-inspect-1.0 is unavailable.'
    NDI_READY=0
fi

NDI_LIBRARY=0
if command -v ldconfig >/dev/null; then
    if ldconfig -p 2>/dev/null | grep -E 'libndi\.so' >/dev/null; then NDI_LIBRARY=1; fi
fi
for runtime_dir in "${NDI_RUNTIME_DIR_V6:-}" "${NDI_RUNTIME_DIR_V5:-}"; do
    [[ -n "$runtime_dir" ]] || continue
    for library in "$runtime_dir"/libndi.so*; do
        if [[ -r "$library" ]]; then NDI_LIBRARY=1; fi
    done
done
log '=== NDI runtime/plugin ==='
if ((NDI_READY && NDI_LIBRARY)); then
    log 'NDI factories and a runtime library were found; stream/discovery/groups operation is not tested by this installer.'
elif ((BASE_ONLY)); then
    warn 'NDI is not ready; excluded by --base-only. This is not a complete NDI installation.'
else
    issue 'NDI is not ready: requires gst-plugin-ndi and a compatible libndi runtime. Distribution GStreamer packages alone do not provide a complete NDI installation.'
    log 'ACTION: use the future verified Converter NDI runtime/plugin packages; they are not published yet. This script does not fetch an unpinned SDK or build plugins on the server.'
fi

log '=== Summary ==='
if ((ISSUES > 0)); then
    log "Unresolved prerequisite checks: $ISSUES. Available distribution components were processed; this host is not fully ready."
    exit 2
fi
log "Selected prerequisite scope satisfied (base-only=$BASE_ONLY). No Converter application or media routes have been installed or started."
