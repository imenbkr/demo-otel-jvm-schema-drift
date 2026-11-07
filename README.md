# JVM metric schema drift: detecting it before dashboards go blank

Upgrading the OpenTelemetry Java agent from 1.x to 2.x renames every JVM metric and most of
their labels. Nothing errors: CPU, heap and GC panels just stop drawing, and alerts built on
those metrics stop evaluating. This repo reproduces that upgrade on
[Spring PetClinic Microservices](https://github.com/spring-petclinic/spring-petclinic-microservices)
and detects the change three ways: in CI before the deploy, at runtime against a contract,
and at runtime with no contract at all.

![JVM dashboard after the agent upgrade: every line stops at 20:43](docs/images/03-jvm-overview-after-upgrade.jpg)

## What changes between agent 1.33.6 and 2.33.1

| Signal | Agent 1.33.6 | Agent 2.33.1 |
|---|---|---|
| Heap / memory used | `process_runtime_jvm_memory_usage_bytes{type, pool}` | `jvm_memory_used_bytes{jvm_memory_type, jvm_memory_pool_name}` |
| Memory limit | `process_runtime_jvm_memory_limit_bytes{type, pool}` | `jvm_memory_limit_bytes{jvm_memory_type, jvm_memory_pool_name}` |
| CPU | `process_runtime_jvm_cpu_utilization_ratio` | `jvm_cpu_recent_utilization` |
| GC | `process_runtime_jvm_gc_duration_seconds{gc, action}` | `jvm_gc_duration_seconds{jvm_gc_name, jvm_gc_action}` |
| Threads | `process_runtime_jvm_threads_count{daemon}` | `jvm_thread_count{jvm_thread_daemon, jvm_thread_state}` |
| Loaded classes | `process_runtime_jvm_classes_current_loaded` | `jvm_class_count` |
| Distinct JVM metric names | 18 | 14 (buffer, init and system CPU metrics are no longer on by default) |
| Agent version label on `target_info` | `telemetry_auto_version` | `telemetry_distro_version` |
| Prometheus exporter listens on | all interfaces | `localhost` only (needs `OTEL_EXPORTER_PROMETHEUS_HOST=0.0.0.0`) |

All captured from the running services: [contract/snapshots/](contract/snapshots/).

## How it detects the change

| When | Check | Catches |
|---|---|---|
| CI, before deploy | [`scripts/check_schema.py`](scripts/check_schema.py) + `mimirtool analyze dashboard` | dashboards or contract using metrics/labels the pinned agent does not emit, with the likely new name |
| Runtime, with contract | `JvmContractMetricMissing`, `JvmContractLabelMissing`, `JvmUnexpectedMetric` | a contract metric or label with no data; metric names outside the schema snapshot |
| Runtime, no contract | `JvmMetricNameAppeared`, `JvmMetricNameDisappeared`, `JvmMetricCountChanged` | any change in the set of JVM metric names compared with 10 minutes ago |

The contract ([contract/jvm-metrics.yaml](contract/jvm-metrics.yaml)) maps each signal the dashboards
need to the metric and label names that currently carry it. Dashboards and alert rules are generated
from it, so fixing a rename is a change to that one file.

![Schema drift dashboard after the upgrade](docs/images/04-schema-drift-red.jpg)

![CI gate output after recording the 2.33.1 schema](docs/images/11-ci-gate-2-renamed-metrics.jpg)

## Run it

Needs Docker and Python 3 with PyYAML. Host ports: app 8180, Prometheus 9190, Grafana 3100.

```bash
make up          # PetClinic + agent from .env, Prometheus, Grafana, k6 traffic
```

Grafana: http://localhost:3100 (dashboards *PetClinic · JVM overview* and *PetClinic · JVM schema drift*).

Reproduce the upgrade:

```bash
sed -i 's/^OTEL_AGENT_VERSION=.*/OTEL_AGENT_VERSION=2.33.1/' .env
make check       # fails: no schema snapshot for 2.33.1
make up          # deploy 2.33.1: dashboards go blank, schema alerts fire
make snapshot    # record what 2.33.1 emits
make check       # fails: lists every renamed metric and label
# edit contract/jvm-metrics.yaml, then
make generate && make check && make reload
```

Clean up (removes containers, volumes and metric data):

```bash
make down
```

## Layout

```
.env                          agent version (the one line an upgrade changes)
docker-compose.yml            PetClinic services, agent download, Prometheus, Grafana, k6
contract/jvm-metrics.yaml     signals the dashboards depend on
contract/snapshots/           JVM metric schema per agent version, captured from Prometheus
scripts/                      snapshot, rule and dashboard generators, CI gate
prometheus/rules/             generated alert rules
grafana/dashboards/           generated dashboards
.github/workflows/            CI gate on every push and pull request
```
