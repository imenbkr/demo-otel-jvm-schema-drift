"""Capture the JVM metric schema (names + label keys) the running agent emits.

Writes contract/snapshots/otel-agent-<version>.yaml for the version pinned in .env.
Run it after deploying a new agent version to a test environment:

    python scripts/snapshot.py --prometheus http://localhost:9190
"""
import argparse
import datetime
import json
import urllib.parse
import urllib.request

import yaml

from common import SNAPSHOTS, agent_version, snapshot_path

# Labels added by Prometheus or the scrape config, not by the agent
IGNORED = {"__name__", "job", "instance", "service", "otel_scope_name", "otel_scope_version"}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--prometheus", default="http://localhost:9190")
    parser.add_argument("--job", default="otel-agent")
    args = parser.parse_args()

    selector = f'{{job="{args.job}", __name__=~".*jvm.*"}}'
    url = f"{args.prometheus}/api/v1/series?" + urllib.parse.urlencode({"match[]": selector})
    series = json.load(urllib.request.urlopen(url))["data"]

    schema = {}
    for s in series:
        schema.setdefault(s["__name__"], set()).update(k for k in s if k not in IGNORED)

    version = agent_version()
    SNAPSHOTS.mkdir(parents=True, exist_ok=True)
    out = {
        "agent_version": version,
        "captured_at": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "metrics": {name: sorted(labels) for name, labels in sorted(schema.items())},
    }
    snapshot_path(version).write_text(yaml.safe_dump(out, sort_keys=False))
    print(f"wrote {snapshot_path(version).relative_to(SNAPSHOTS.parent.parent)}: {len(schema)} metrics")


if __name__ == "__main__":
    main()
