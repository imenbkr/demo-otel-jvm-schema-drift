# JVM metric schema drift: detecting it before dashboards go blank

Upgrading the OpenTelemetry Java agent from 1.x to 2.x renames every JVM metric and most of
their labels. Nothing errors: CPU, heap and GC panels just stop drawing, and alerts built on
those metrics stop evaluating. This repo reproduces that upgrade on
[Spring PetClinic Microservices](https://github.com/spring-petclinic/spring-petclinic-microservices)
and detects the change three ways: in CI before the deploy, at runtime against a contract,
and at runtime with no contract at all.

![JVM dashboard after the agent upgrade: every line stops at 20:43](docs/images/03-jvm-overview-after-upgrade.jpg)

## Architecture

Each PetClinic service runs with the OpenTelemetry Java agent attached (`-javaagent`). The agent
reads the JVM runtime (memory, GC, threads, CPU) and serves it in Prometheus format on `:9464`.
Prometheus scrapes it and evaluates the schema-drift rules; Grafana queries Prometheus.

```mermaid
flowchart LR
  k6["k6 load test"] -->|HTTP| gw

  subgraph app["Spring PetClinic"]
    direction LR
    gw["api-gateway<br/>☕ + OTel Java agent"]
    subgraph svc["Business services"]
      direction TB
      cust["customers-service<br/>☕ + OTel Java agent"]
      vis["visits-service<br/>☕ + OTel Java agent"]
      vets["vets-service<br/>☕ + OTel Java agent"]
    end
    gw --> cust & vis & vets
  end

  subgraph support["Support: config, discovery, agent jar"]
    direction TB
    cfg["config-server"]
    disc["discovery-server (Eureka)"]
    dl["otel-agent init<br/>downloads the agent jar<br/>(version from .env)"]
  end
  support -.-> app

  app -->|"scrape :9464/metrics<br/>every 15s"| prom["Prometheus<br/>JVM metrics +<br/>schema-drift rules"]
  prom -->|PromQL| graf["Grafana<br/>JVM overview<br/>JVM schema drift"]

  classDef jvm fill:#e8f0fe,stroke:#1a73e8,color:#0b2a5b
  classDef obs fill:#fef3e0,stroke:#e37400,color:#4a2600
  classDef infra fill:#f1f3f4,stroke:#80868b,color:#202124
  class gw,cust,vis,vets jvm
  class prom,graf obs
  class cfg,disc,dl,k6 infra
```

### From the agent to a Grafana panel

The upgrade changes only what the agent emits. Everything downstream (the scrape, the PromQL, the
panel) keeps asking for the old names, which is why nothing errors.

```mermaid
flowchart LR
  subgraph jvm["Service container"]
    direction TB
    code["Spring Boot app"]
    agent["OTel Java agent<br/>(-javaagent, same JVM)<br/>reads memory, GC,<br/>threads, CPU"]
    exp["Prometheus exporter<br/>:9464/metrics"]
    code -.- agent
    agent --> exp
  end
  exp -->|pull| prom["Prometheus"]
  prom -->|PromQL| graf["Grafana panel<br/>Heap used"]

  exp -.->|"agent 1.33.6"| v1["process_runtime_jvm_memory_usage_bytes<br/>{type, pool}"]:::old
  exp -.->|"agent 2.33.1"| v2["jvm_memory_used_bytes<br/>{jvm_memory_type, jvm_memory_pool_name}"]:::new

  classDef old fill:#fce8e6,stroke:#d93025,color:#5b0f0a
  classDef new fill:#e6f4ea,stroke:#1e8e3e,color:#0d3d1b
```

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

```mermaid
flowchart TB
  contract["contract/jvm-metrics.yaml<br/>signal → metric + label names"]
  contract -->|build_dashboard.py| dash["Grafana dashboards"]
  contract -->|build_rules.py| rules["Prometheus alert rules"]

  subgraph ci["① CI gate (make check)"]
    direction LR
    mimir["mimirtool analyze dashboard<br/>metrics the dashboards query"]
    snap["snapshots/otel-agent-#lt;version#gt;.yaml<br/>what the agent really emits"]
    check["check_schema.py<br/>missing metric or label?<br/>→ likely new name"]
    mimir --> check
    snap --> check
  end
  dash --> mimir
  contract --> check
  env[".env<br/>OTEL_AGENT_VERSION"] --> check
  testprom["Prometheus (test env)"] -->|snapshot.py| snap

  subgraph rt["Runtime (Prometheus)"]
    direction LR
    r2["② Contract rules<br/>JvmContractMetricMissing<br/>JvmContractLabelMissing<br/>JvmUnexpectedMetric"]
    r3["③ Change rules, no contract<br/>JvmMetricNameAppeared<br/>JvmMetricNameDisappeared<br/>JvmMetricCountChanged"]
  end
  rules --> r2 & r3
  r2 & r3 --> drift["Grafana: JVM schema drift"]

  classDef gate fill:#e8f0fe,stroke:#1a73e8,color:#0b2a5b
  classDef run fill:#fef3e0,stroke:#e37400,color:#4a2600
  class mimir,snap,check gate
  class r2,r3,drift run
```

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
