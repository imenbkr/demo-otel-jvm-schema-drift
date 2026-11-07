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


def closest(name, candidates):
    """Best guess for what a missing metric was renamed to."""
    # Compare without the 1.x prefix so process_runtime_jvm_threads_count ~ jvm_thread_count
    key = name.replace("process_runtime_", "")
    scored = sorted(candidates, key=lambda c: difflib.SequenceMatcher(None, key, c).ratio(), reverse=True)
    return scored[0] if scored and difflib.SequenceMatcher(None, key, scored[0]).ratio() > 0.6 else None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dashboard-metrics", required=True, help="JSON output of `mimirtool analyze dashboard`")
    args = parser.parse_args()

    version = agent_version()
    snapshot = load_snapshot(version)
    problems = []
    if snapshot is None:
        problems.append((".env", f"no schema snapshot for agent {version}: deploy it to a test environment and run "
                                 f"`python scripts/snapshot.py` to create {snapshot_path(version).relative_to(ROOT)}"))
    else:
        known = snapshot["metrics"]
        new_names = sorted(set(known))

        used = json.load(open(args.dashboard_metrics))["metricsUsed"]
        for metric in sorted(m for m in used if "jvm" in m and m not in known):
            hint = closest(metric, new_names)
            problems.append(("grafana/dashboards", f"dashboards query {metric}, which agent {version} does not emit"
                                                   + (f" (renamed to {hint}?)" if hint else "")))

        for signal_name, signal in load_contract()["signals"].items():
            for metric in series_names(signal):
                if metric.endswith("_bucket") and metric not in known:
                    continue  # histogram buckets are optional for our dashboards
                if metric not in known:
                    hint = closest(metric, new_names)
                    problems.append(("contract/jvm-metrics.yaml",
                                     f"signal '{signal_name}': {metric} is not emitted by agent {version}"
                                     + (f" (renamed to {hint}?)" if hint else "")))
            probe = probe_name(signal)
            if probe in known:
                for logical, label in (signal.get("labels") or {}).items():
                    if label not in known[probe]:
                        problems.append(("contract/jvm-metrics.yaml",
                                         f"signal '{signal_name}': {probe} has no label '{label}' in agent {version} "
                                         f"(labels: {', '.join(known[probe])})"))

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
