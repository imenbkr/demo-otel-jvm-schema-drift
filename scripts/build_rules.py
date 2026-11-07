"""Generate Prometheus alert rules that detect JVM metric schema changes.

Two families of rules:
  * contract rules: the metrics and labels in contract/jvm-metrics.yaml must exist, and no
    JVM metric outside the snapshot of the pinned agent version should appear;
  * change rules: compare the set of JVM metric names with WINDOW ago, with no list at all
    (catches drift even when nobody updated the contract).

    python scripts/build_rules.py            # writes prometheus/rules/jvm-schema.yml
"""
import argparse

import yaml

from common import ROOT, agent_version, load_contract, load_snapshot, probe_name

OUT = ROOT / "prometheus" / "rules" / "jvm-schema.yml"


def as_metric_label(expr):
    """Alerts drop __name__, so copy the metric name into a `metric` label."""
    return f'label_replace({expr}, "metric", "$1", "__name__", "(.+)")'


def contract_rules(contract, snapshot):
    job = contract["job"]
    rules = []
    for name, signal in contract["signals"].items():
        probe = probe_name(signal)
        rules.append({
            "alert": "JvmContractMetricMissing",
            "expr": f'group by (service) (up{{job="{job}"}} == 1)\nunless\ngroup by (service) ({probe}{{job="{job}"}})',
            "for": "1m",
            "labels": {"severity": "critical", "signal": name, "metric": probe},
            "annotations": {
                "summary": f"{{{{ $labels.service }}}} stopped emitting {probe}",
                "description": f"Signal '{name}' in the JVM metrics contract has no data. "
                               "Dashboards and alerts built on it now show 'No data'.",
            },
        })
        labels = list((signal.get("labels") or {}).values())
        if labels:
            present = ", ".join(f'{label}!=""' for label in labels)
            rules.append({
                "alert": "JvmContractLabelMissing",
                "expr": f'group by (service) ({probe}{{job="{job}"}})\nunless\n'
                        f'group by (service) ({probe}{{job="{job}", {present}}})',
                "for": "1m",
                "labels": {"severity": "critical", "signal": name, "metric": probe},
                "annotations": {
                    "summary": f"{{{{ $labels.service }}}}: {probe} lost label(s) {', '.join(labels)}",
                    "description": "The metric still exists but a label the dashboards group or filter by is gone.",
                },
            })
    if snapshot:
        known = "|".join(sorted(snapshot["metrics"]))
        expr = f'group by (__name__, service) ({{job="{job}", __name__=~".*jvm.*", __name__!~"{known}"}})'
        rules.append({
            "alert": "JvmUnexpectedMetric",
            "expr": as_metric_label(expr),
            "for": "1m",
            "labels": {"severity": "warning"},
            "annotations": {
                "summary": "{{ $labels.service }} emits {{ $labels.metric }}, which is not in the "
                           f"schema snapshot of agent {snapshot['agent_version']}",
                "description": "A new JVM metric name appeared. Often half of a rename: check JvmContractMetricMissing.",
            },
        })
    return rules


def change_rules(job, window):
    names = f'group by (__name__, service) ({{job="{job}", __name__=~".*jvm.*"}})'
    names_before = f'group by (__name__, service) ({{job="{job}", __name__=~".*jvm.*"}} offset {window})'
    # Only compare when the service was actually scraped at both points in time
    up_before = f'group by (service) (up{{job="{job}"}} offset {window} == 1)'
    up_now = f'group by (service) (up{{job="{job}"}} == 1)'
    return [
        {
            "alert": "JvmMetricNameAppeared",
            "expr": as_metric_label(f"({names}\nunless\n{names_before})\nand on (service) {up_before}"),
            "labels": {"severity": "info"},
            "annotations": {"summary": f"New JVM metric {{{{ $labels.metric }}}} on {{{{ $labels.service }}}} (not there {window} ago)"},
        },
        {
            "alert": "JvmMetricNameDisappeared",
            "expr": as_metric_label(f"({names_before}\nunless\n{names})\nand on (service) {up_now}"),
            "labels": {"severity": "warning"},
            "annotations": {"summary": f"JVM metric {{{{ $labels.metric }}}} on {{{{ $labels.service }}}} disappeared in the last {window}"},
        },
        {
            # Dynatrace-style "count the fields": the number of distinct JVM metric names per service
            "alert": "JvmMetricCountChanged",
            "expr": f"count by (service) ({names})\n!= on (service)\ncount by (service) ({names_before})",
            "labels": {"severity": "warning"},
            "annotations": {"summary": f"{{{{ $labels.service }}}} now emits {{{{ $value }}}} JVM metric names (changed in the last {window})"},
        },
    ]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--window", default="10m", help="look-back for change rules (demo: 10m, production: 1h or more)")
    args = parser.parse_args()

    contract = load_contract()
    snapshot = load_snapshot(agent_version())
    rules = {"groups": [
        {"name": "jvm-schema-contract", "rules": contract_rules(contract, snapshot)},
        {"name": "jvm-schema-change", "rules": change_rules(contract["job"], args.window)},
    ]}
    OUT.write_text("# Generated by scripts/build_rules.py from contract/jvm-metrics.yaml. Do not edit.\n"
                   + yaml.safe_dump(rules, sort_keys=False, width=200))
    print(f"wrote {OUT.relative_to(ROOT)}: {sum(len(g['rules']) for g in rules['groups'])} rules")


if __name__ == "__main__":
    main()
