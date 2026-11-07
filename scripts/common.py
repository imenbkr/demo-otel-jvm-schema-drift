"""Shared helpers: paths, the pinned agent version, the contract and schema snapshots."""
import pathlib
import re

import yaml

ROOT = pathlib.Path(__file__).resolve().parent.parent
CONTRACT = ROOT / "contract" / "jvm-metrics.yaml"
SNAPSHOTS = ROOT / "contract" / "snapshots"


def agent_version():
    match = re.search(r"^OTEL_AGENT_VERSION=(\S+)", (ROOT / ".env").read_text(), re.M)
    return match.group(1)


def snapshot_path(version):
    return SNAPSHOTS / f"otel-agent-{version}.yaml"


def load_contract():
    return yaml.safe_load(CONTRACT.read_text())


def load_snapshot(version):
    path = snapshot_path(version)
    return yaml.safe_load(path.read_text()) if path.exists() else None


def series_names(signal):
    """Metric names Prometheus stores for a signal (a histogram is stored as _count/_sum/_bucket)."""
    m = signal["metric"]
    return [f"{m}_count", f"{m}_sum", f"{m}_bucket"] if signal.get("histogram") else [m]


def probe_name(signal):
    """One series name that is always present when the signal is."""
    return series_names(signal)[0]
