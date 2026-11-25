"""Read-only Linux acceptance of three running SDI nodes wired as a hardware loopback."""

import argparse
from fractions import Fraction
import json
from pathlib import Path
import sys
import time

from converter.runtime.client import RuntimeClient


def snapshot(client, node_ids):
    """Read the normal runtime statuses and verify the three roles and wiring in the graph."""
    workers = [client.call("status", node_id=node_id) for node_id in node_ids]
    if any(item["state"] != "running" for item in workers):
        raise RuntimeError("All three graph nodes must be running: " + ", ".join(
            f"{node_id}={item['state']}" for node_id, item in zip(node_ids, workers)))
    statuses = [item["status"] for item in workers]
    if [item["node_type"] for item in statuses] != ["sdi-input", "sdi-output", "sdi-input"]:
        raise RuntimeError("Provide UUIDs for source input, SDI output, and loopback input in that order.")
    metrics = [item["metrics"] for item in statuses]
    if not all(item.get("generation") for item in metrics) or metrics[0]["generation"] != metrics[1]["generation"]:
        raise RuntimeError("Wait for source/consumer generation metrics and check the output connection.")
    if any("av_pairs" not in metrics[index] for index in (0, 2)):
        raise RuntimeError("Enable the A/V marker measurement on both input nodes.")
    return metrics


def average(before, after):
    """Calculate the signed average A/V offset over only the newly captured markers."""
    return (after["av_sum_ns"] - before["av_sum_ns"]) / (after["av_pairs"] - before["av_pairs"])


def collect(client, node_ids, baseline, wanted, deadline):
    """Wait for fresh marker pairs without combining generations or ignoring route loss."""
    while time.monotonic() < deadline:
        now = snapshot(client, node_ids)
        if any(old["generation"] != new["generation"] for old, new in zip(baseline, now)):
            raise RuntimeError("An SDI signal or transport generation changed during measurement.")
        if all(now[index]["av_pairs"] - baseline[index]["av_pairs"] >= wanted for index in (0, 2)):
            return now
        time.sleep(1)
    raise RuntimeError("Too few flash/click pairs. Check the physical loopback and marker levels.")


def check_route(client, node_ids, pairs, deadline, budget_ns):
    """Compare two windows of physical A/V offsets and check route loss counters."""
    baseline = snapshot(client, node_ids)
    middle = collect(client, node_ids, baseline, pairs // 2, deadline)
    end = collect(client, node_ids, middle, pairs - pairs // 2, deadline)
    first_added = average(baseline[2], middle[2]) - average(baseline[0], middle[0])
    second_added = average(middle[2], end[2]) - average(middle[0], end[0])
    losses = {}
    capture = ("shm_drops", "capture_discontinuities")
    for index, keys in ((0, capture), (1, ("late_drops", "shm_skipped")), (2, capture)):
        for key in keys:
            losses[f"{index}:{key}"] = end[index][key] - baseline[index][key]
    report = {
        "direct_pairs": end[0]["av_pairs"] - baseline[0]["av_pairs"],
        "loopback_pairs": end[2]["av_pairs"] - baseline[2]["av_pairs"],
        "direct_audio_minus_video_ms": average(baseline[0], end[0]) / 1_000_000,
        "loopback_audio_minus_video_ms": average(baseline[2], end[2]) / 1_000_000,
        "direct_range_ms_since_signal_lock": [end[0][key] / 1_000_000 for key in ("av_min_ns", "av_max_ns")],
        "loopback_range_ms_since_signal_lock": [end[2][key] / 1_000_000 for key in ("av_min_ns", "av_max_ns")],
        "added_skew_ms_first_half": first_added / 1_000_000,
        "added_skew_ms_second_half": second_added / 1_000_000,
        "added_skew_drift_ms": (second_added - first_added) / 1_000_000,
        "threshold_ms": budget_ns / 1_000_000,
        "losses": losses,
    }
    print(json.dumps(report, indent=2))
    if max(abs(first_added), abs(second_added), abs(second_added - first_added)) > budget_ns:
        raise RuntimeError("Added A/V offset or drift exceeded the selected budget.")
    if any(losses.values()):
        raise RuntimeError("The route reported dropped frames during measurement.")
    print("Physical SDI loopback A/V acceptance passed")


def main():
    """Validate options and query the installed release as the runtime service user."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source")
    parser.add_argument("output")
    parser.add_argument("loopback")
    parser.add_argument("--fps", type=Fraction, default=Fraction(25), help="Picture rate of the slower route leg.")
    parser.add_argument("--pairs", type=int, default=30)
    parser.add_argument("--timeout", type=int, default=90)
    parser.add_argument("--socket", default="/run/ip-media-tool/runtime.sock")
    args = parser.parse_args()
    if sys.platform != "linux":
        parser.error("Run on Linux only.")
    node_ids = (args.source, args.output, args.loopback)
    if len(set(node_ids)) != 3 or min(args.fps, args.timeout) <= 0 or args.pairs < 4:
        parser.error("Three distinct nodes, a positive picture rate and timeout, and at least four pairs are required.")
    root = Path(__file__).resolve().parents[1]
    client = RuntimeClient(args.socket, root.name)
    try:
        check_route(client, node_ids, args.pairs, time.monotonic() + args.timeout, float(1_000_000_000 / args.fps))
    except RuntimeError as error:
        raise SystemExit(str(error)) from None


if __name__ == "__main__":
    main()
