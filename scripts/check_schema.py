"""CI gate: do our dashboards and contract still match the agent version we are about to deploy?

Inputs:
  * the agent version pinned in .env,
  * contract/snapshots/otel-agent-<version>.yaml (what that version really emits),
  * contract/jvm-metrics.yaml,
  * the metrics the dashboards query, extracted by `mimirtool analyze dashboard`.

    python scripts/check_schema.py --dashboard-metrics metrics-in-grafana.json
"""
import argparse
import difflib
import json
import os
import sys

from common import agent_version, load_contract, load_snapshot, probe_name, series_names, snapshot_path, ROOT


# Renames from the OpenTelemetry JVM semantic conventions (agent 1.x -> 2.x), Prometheus spelling
KNOWN_RENAMES = {
    "process_runtime_jvm_memory_usage_bytes": "jvm_memory_used_bytes",
    "process_runtime_jvm_memory_committed_bytes": "jvm_memory_committed_bytes",
    "process_runtime_jvm_memory_limit_bytes": "jvm_memory_limit_bytes",
    "process_runtime_jvm_memory_usage_after_last_gc_bytes": "jvm_memory_used_after_last_gc_bytes",
    "process_runtime_jvm_gc_duration_seconds": "jvm_gc_duration_seconds",
    "process_runtime_jvm_threads_count": "jvm_thread_count",
    "process_runtime_jvm_classes_loaded_total": "jvm_class_loaded_total",
    "process_runtime_jvm_classes_unloaded_total": "jvm_class_unloaded_total",
    "process_runtime_jvm_classes_current_loaded": "jvm_class_count",
    "process_runtime_jvm_cpu_utilization_ratio": "jvm_cpu_recent_utilization",
}


def closest(name, candidates):
    """Best guess for what a missing metric was renamed to."""
    for old, new in KNOWN_RENAMES.items():
        if name.startswith(old):
            renamed = new + name[len(old):]  # keep _count / _sum / _bucket
            if renamed in candidates:
                return renamed
    # Otherwise compare without the 1.x prefix so process_runtime_jvm_x ~ jvm_x
    key = name.replace("process_runtime_", "")
    scored = sorted(candidates, key=lambda c: difflib.SequenceMatcher(None, key, c).ratio(), reverse=True)
    return scored[0] if scored and difflib.SequenceMatcher(None, key, scored[0]).ratio() > 0.6 else None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dashboard-metrics", required=True, help="JSON output of `mimirtool analyze dashboard`")
    args = parser.parse_args()

    version = agent_version()
    snapshot = load_snapshot(version)
    if snapshot is None:
        problems = [(".env", f"no schema snapshot for agent {version}: deploy it to a test environment and run "
                             f"`python scripts/snapshot.py` to create {snapshot_path(version).relative_to(ROOT)}")]
    else:
        used = json.load(open(args.dashboard_metrics))["metricsUsed"]
        problems = check_dashboards(used, snapshot["metrics"], version) + check_contract(snapshot["metrics"], version)
    report_and_exit(problems, version)


def renamed_hint(metric, known):
    hint = closest(metric, sorted(known))
    return f" (renamed to {hint}?)" if hint else ""


def check_dashboards(used, known, version):
    """Every JVM metric a dashboard queries must exist in the new schema."""
    return [("grafana/dashboards", f"dashboards query {metric}, which agent {version} does not emit"
             + renamed_hint(metric, known))
            for metric in sorted(m for m in used if "jvm" in m and m not in known)]


def check_contract(known, version):
    """Every contract signal must exist in the new schema, with the labels the dashboards use."""
    problems = []
    for signal_name, signal in load_contract()["signals"].items():
        # histogram buckets are optional for our dashboards
        for metric in (m for m in series_names(signal) if m not in known and not m.endswith("_bucket")):
            problems.append(("contract/jvm-metrics.yaml", f"signal '{signal_name}': {metric} is not emitted by "
                             f"agent {version}" + renamed_hint(metric, known)))
        probe = probe_name(signal)
        missing_labels = [label for label in (signal.get("labels") or {}).values()
                          if probe in known and label not in known[probe]]
        for label in missing_labels:
            problems.append(("contract/jvm-metrics.yaml", f"signal '{signal_name}': {probe} has no label '{label}' "
                             f"in agent {version} (labels: {', '.join(known[probe])})"))
    return problems


def report_and_exit(problems, version):
    report = [f"## JVM metric schema check (agent {version})", ""]
    if problems:
        report += [f"**{len(problems)} problem(s)**: dashboards or alerts would show 'No data' after this deploy.", ""]
        report += [f"- `{where}`: {what}" for where, what in problems]
    else:
        report.append("Dashboards and contract match the schema this agent version emits.")
    print("\n".join(report))
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a") as f:
            f.write("\n".join(report) + "\n")
    for where, what in problems:
        print(f"::error file={where}::{what}")
    sys.exit(1 if problems else 0)


if __name__ == "__main__":
    main()
