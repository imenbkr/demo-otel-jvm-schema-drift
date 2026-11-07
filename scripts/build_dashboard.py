"""Generate the Grafana dashboards.

  * jvm-overview.json: CPU, memory, GC, threads and classes per service. Every query takes
    its metric and label names from contract/jvm-metrics.yaml.
  * jvm-schema-drift.json: watches the schema itself (metric count, agent version,
    firing schema alerts, metrics outside the snapshot). Uses no JVM metric names.

    python scripts/build_dashboard.py
"""
import json

from common import ROOT, load_contract

OUT = ROOT / "grafana" / "dashboards"
DS = {"type": "prometheus", "uid": "prometheus"}


class Board:
    def __init__(self):
        self.panels, self.y = [], 0

    def row(self, title):
        self.panels.append({"type": "row", "title": title, "collapsed": False, "panels": [],
                            "gridPos": {"x": 0, "y": self.y, "w": 24, "h": 1}, "id": len(self.panels) + 1})
        self.y += 1

    def panel(self, kind, title, targets, x, w, h, unit="short", desc="", **extra):
        p = {"type": kind, "title": title, "description": desc, "datasource": DS, "id": len(self.panels) + 1,
             "gridPos": {"x": x, "y": self.y, "w": w, "h": h},
             "targets": [{"datasource": DS, "refId": chr(65 + i), "expr": e, "legendFormat": legend, **opts}
                         for i, (e, legend, opts) in enumerate(targets)],
             "fieldConfig": {"defaults": {"unit": unit, **extra.pop("defaults", {})}, "overrides": []},
             "options": extra.pop("options", {})}
        p.update(extra)
        self.panels.append(p)

    def dashboard(self, uid, title, description, time_from="now-1h"):
        return {"uid": uid, "title": title, "description": description, "tags": ["jvm", "schema-drift"],
                "timezone": "browser", "schemaVersion": 41, "refresh": "15s",
                "time": {"from": time_from, "to": "now"}, "panels": self.panels,
                "annotations": {"list": [DEPLOY_MARKERS]}}


# Vertical markers for deploys: `make up` posts a Grafana annotation tagged "deploy"
DEPLOY_MARKERS = {
    "name": "Deploys", "enable": True, "iconColor": "#FF9830",
    "datasource": {"type": "grafana", "uid": "-- Grafana --"},
    "target": {"type": "tags", "tags": ["deploy"], "limit": 100, "matchAny": True},
}


def q(expr, legend="{{service}}", **opts):
    return (expr, legend, opts)


TS = {"defaults": {"custom": {"lineWidth": 2, "fillOpacity": 10}}}


def jvm_overview(contract):
    job = contract["job"]
    s = contract["signals"]

    def m(signal, suffix=""):
        return s[signal]["metric"] + suffix

    def lbl(signal, name):
        return s[signal]["labels"][name]

    heap = f'{m("memory_used")}{{job="{job}", {lbl("memory_used", "type")}="heap"}}'
    heap_limit = f'{m("memory_limit")}{{job="{job}", {lbl("memory_limit", "type")}="heap"}}'
    b = Board()
    b.row("CPU and memory")
    b.panel("timeseries", "CPU used by the JVM", [q(f'max by (service) ({m("cpu_utilization")}{{job="{job}"}})')],
            0, 8, 8, "percentunit", "Share of the container CPU used by the JVM process.", **TS)
    b.panel("timeseries", "Heap used", [q(f"sum by (service) ({heap})")], 8, 8, 8, "bytes",
            "Java heap memory in use, summed over all heap pools.", **TS)
    b.panel("timeseries", "Heap used vs limit", [q(f"sum by (service) ({heap}) / sum by (service) ({heap_limit} > 0)")],
            16, 8, 8, "percentunit", "Above 90% for long means the service is close to OutOfMemoryError.",
            defaults={"max": 1, "min": 0, "custom": {"lineWidth": 2, "fillOpacity": 10}})
    b.y += 8
    b.row("Garbage collection, threads and classes")
    b.panel("timeseries", "Time spent in GC (per second)",
            [q(f'sum by (service) (rate({m("gc_duration", "_sum")}{{job="{job}"}}[2m]))')], 0, 8, 8, "s",
            "Seconds of garbage collection per second of wall time.", **TS)
    b.panel("timeseries", "GC runs per second by collector",
            [q(f'sum by (service, {lbl("gc_duration", "gc")}) (rate({m("gc_duration", "_count")}{{job="{job}"}}[2m]))',
               "{{service}} · {{" + lbl("gc_duration", "gc") + "}}")], 8, 8, 8, "ops", **TS)
    b.panel("timeseries", "Live threads",
            [q(f'sum by (service) ({m("threads")}{{job="{job}"}})')], 16, 4, 8, "short", **TS)
    b.panel("timeseries", "Loaded classes",
            [q(f'max by (service) ({m("classes_loaded")}{{job="{job}"}})')], 20, 4, 8, "short", **TS)
    b.y += 8
    return b.dashboard("jvm-overview", "PetClinic · JVM overview",
                       "CPU, memory, GC and threads of the PetClinic services, from the OpenTelemetry Java agent.")


def schema_drift(contract):
    job = contract["job"]
    names = f'group by (__name__, service) ({{job="{job}", __name__=~".*jvm.*"}})'
    b = Board()
    b.row("Is the JVM metric schema still the one our dashboards expect?")
    b.panel("stat", "Contract metrics missing",
            [q('count(ALERTS{alertname="JvmContractMetricMissing", alertstate="firing"}) or vector(0)', "")],
            0, 4, 5, desc="Metrics from contract/jvm-metrics.yaml with no data, summed over services.",
            defaults={"thresholds": {"mode": "absolute", "steps": [{"color": "green", "value": None}, {"color": "red", "value": 1}]}},
            options={"colorMode": "background", "graphMode": "none", "reduceOptions": {"calcs": ["lastNotNull"]}})
    b.panel("stat", "Unexpected JVM metrics",
            [q('count(ALERTS{alertname="JvmUnexpectedMetric", alertstate="firing"}) or vector(0)', "")],
            4, 4, 5, desc="JVM metric names not in the schema snapshot of the pinned agent version.",
            defaults={"thresholds": {"mode": "absolute", "steps": [{"color": "green", "value": None}, {"color": "orange", "value": 1}]}},
            options={"colorMode": "background", "graphMode": "none", "reduceOptions": {"calcs": ["lastNotNull"]}})
    b.panel("timeseries", "Distinct JVM metric names per service",
            [q(f"count by (service) ({names})")], 8, 8, 5, "short",
            "The 'count the fields' check: a step means metrics were added, removed or renamed.",
            defaults={"custom": {"lineWidth": 2, "lineInterpolation": "stepAfter"}})
    b.panel("timeseries", "OpenTelemetry agent version",
            [q(f'count by (telemetry_auto_version, telemetry_distro_version) (target_info{{job="{job}"}})',
               "{{telemetry_auto_version}}{{telemetry_distro_version}}")], 16, 8, 5, "short",
            "Services per agent version. 1.x reports telemetry_auto_version, 2.x telemetry_distro_version.",
            defaults={"custom": {"lineWidth": 2, "lineInterpolation": "stepAfter", "fillOpacity": 20}})
    b.y += 5
    b.panel("table", "Firing schema alerts",
            [q('ALERTS{alertname=~"Jvm.*", alertstate="firing"}', "", instant=True, format="table")],
            0, 24, 9, desc="Generated from the contract by scripts/build_rules.py.",
            defaults={"noValue": "No schema alerts firing"},
            transformations=[{"id": "organize", "options": {
                "excludeByName": {"Time": True, "Value": True, "__name__": True, "alertstate": True},
                "indexByName": {"severity": 0, "alertname": 1, "service": 2, "metric": 3, "signal": 4}}}],
            options={"sortBy": [{"displayName": "severity"}]})
    b.y += 9
    return b.dashboard("jvm-schema-drift", "PetClinic · JVM schema drift",
                       "Detects changes in the JVM metric schema: missing contract metrics, new names, count changes.")


def main():
    contract = load_contract()
    for name, board in (("jvm-overview", jvm_overview(contract)), ("jvm-schema-drift", schema_drift(contract))):
        (OUT / f"{name}.json").write_text(json.dumps(board, indent=2) + "\n")
        print(f"wrote grafana/dashboards/{name}.json: {len([p for p in board['panels'] if p['type'] != 'row'])} panels")


if __name__ == "__main__":
    main()
