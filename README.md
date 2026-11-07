# JVM metric schema drift: detecting it before dashboards go blank

Upgrading the OpenTelemetry Java agent from 1.x to 2.x renames every JVM metric and most of
their labels. Nothing errors: CPU, heap and GC panels just stop drawing, and alerts built on
those metrics stop evaluating. This repo reproduces that upgrade on
[Spring PetClinic Microservices](https://github.com/spring-petclinic/spring-petclinic-microservices)
and detects the change three ways: in CI before the deploy, at runtime against a contract,
and at runtime with no contract at all.

![JVM dashboard after the agent upgrade: every line stops at the 2.33.1 deploy marker](docs/images/03-jvm-overview-after-upgrade.jpg)

## Architecture

Each PetClinic service runs with the OpenTelemetry Java agent attached (`-javaagent`). The agent
reads the JVM runtime (memory, GC, threads, CPU) and serves it in Prometheus format on `:9464`.
Prometheus scrapes it and evaluates the schema-drift rules; Grafana queries Prometheus.

```mermaid
%%{init: {"theme": "base", "themeVariables": {"fontFamily": "Arial, Helvetica, sans-serif", "fontSize": "16px", "primaryColor": "#ffffff", "primaryBorderColor": "#8c959f", "primaryTextColor": "#1f2328", "lineColor": "#6e7781", "clusterBkg": "#f6f8fa", "clusterBorder": "#d0d7de", "edgeLabelBackground": "#ffffff", "titleColor": "#57606a"}, "flowchart": {"curve": "basis", "nodeSpacing": 45, "rankSpacing": 55, "padding": 18}}}%%
flowchart LR
  k6("<b>k6</b><br/>load test"):::infra -->|HTTP| gw

  subgraph app["Spring PetClinic"]
    direction LR
    gw("<b>api-gateway</b><br/>☕ OTel Java agent"):::jvm
    subgraph svc["Business services"]
      direction TB
      cust("<b>customers-service</b><br/>☕ OTel Java agent"):::jvm
      vis("<b>visits-service</b><br/>☕ OTel Java agent"):::jvm
      vets("<b>vets-service</b><br/>☕ OTel Java agent"):::jvm
    end
    gw --> cust & vis & vets
  end

  subgraph support["Support: config, discovery, agent jar"]
    direction TB
    cfg("config-server"):::infra
    disc("discovery-server<br/>(Eureka)"):::infra
    dl("<b>otel-agent init</b><br/>downloads the agent jar<br/>version from .env"):::infra
  end
  support -.-> app

  app -->|"scrape :9464/metrics<br/>every 15s"| prom("<b>Prometheus</b><br/>JVM metrics<br/>schema-drift rules"):::prom
  prom -->|PromQL| graf("<b>Grafana</b><br/>JVM overview<br/>JVM schema drift"):::graf

  classDef jvm fill:#eaf2ff,stroke:#2f6feb,stroke-width:2px,color:#0a3069
  classDef prom fill:#fff0eb,stroke:#e6522c,stroke-width:2px,color:#5c1a06
  classDef graf fill:#fff4e5,stroke:#f46800,stroke-width:2px,color:#5c2a00
  classDef infra fill:#ffffff,stroke:#8c959f,stroke-width:1.5px,color:#24292f
  classDef gate fill:#f3eeff,stroke:#8250df,stroke-width:2px,color:#3e1f79
  classDef ok fill:#dafbe1,stroke:#1a7f37,stroke-width:2px,color:#0f3d1c
  classDef bad fill:#ffebe9,stroke:#cf222e,stroke-width:2px,color:#6e1a1f
  classDef warn fill:#fff8c5,stroke:#bf8700,stroke-width:2px,color:#4d2d00
```

### From the agent to a Grafana panel

The upgrade changes only what the agent emits. Everything downstream (the scrape, the PromQL, the
panel) keeps asking for the old names, which is why nothing errors.

```mermaid
%%{init: {"theme": "base", "themeVariables": {"fontFamily": "Arial, Helvetica, sans-serif", "fontSize": "16px", "primaryColor": "#ffffff", "primaryBorderColor": "#8c959f", "primaryTextColor": "#1f2328", "lineColor": "#6e7781", "clusterBkg": "#f6f8fa", "clusterBorder": "#d0d7de", "edgeLabelBackground": "#ffffff", "titleColor": "#57606a"}, "flowchart": {"curve": "basis", "nodeSpacing": 45, "rankSpacing": 55, "padding": 18}}}%%
flowchart LR
  subgraph jvm["Service container"]
    direction TB
    code("<b>Spring Boot app</b>"):::infra
    agent("<b>OTel Java agent</b><br/>-javaagent, same JVM<br/>reads memory, GC,<br/>threads, CPU"):::jvm
    exp("<b>Prometheus exporter</b><br/>:9464/metrics"):::jvm
    code -.- agent
    agent --> exp
  end
  exp -->|pull| prom("<b>Prometheus</b>"):::prom
  prom -->|PromQL| graf("<b>Grafana panel</b><br/>Heap used"):::graf

  exp -.->|"agent 1.33.6"| v1("process_runtime_jvm_memory_usage_bytes<br/>{type, pool}"):::bad
  exp -.->|"agent 2.33.1"| v2("jvm_memory_used_bytes<br/>{jvm_memory_type, jvm_memory_pool_name}"):::ok

  classDef jvm fill:#eaf2ff,stroke:#2f6feb,stroke-width:2px,color:#0a3069
  classDef prom fill:#fff0eb,stroke:#e6522c,stroke-width:2px,color:#5c1a06
  classDef graf fill:#fff4e5,stroke:#f46800,stroke-width:2px,color:#5c2a00
  classDef infra fill:#ffffff,stroke:#8c959f,stroke-width:1.5px,color:#24292f
  classDef gate fill:#f3eeff,stroke:#8250df,stroke-width:2px,color:#3e1f79
  classDef ok fill:#dafbe1,stroke:#1a7f37,stroke-width:2px,color:#0f3d1c
  classDef bad fill:#ffebe9,stroke:#cf222e,stroke-width:2px,color:#6e1a1f
  classDef warn fill:#fff8c5,stroke:#bf8700,stroke-width:2px,color:#4d2d00
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

**Old → new mapping.** Detection shows which names disappeared and which appeared, but not which replaces which.
The CI gate takes that mapping from OpenTelemetry's official list: the
[Java agent 2.0.0 release notes](https://github.com/open-telemetry/opentelemetry-java-instrumentation/releases/tag/v2.0.0)
("Stable JVM semantic conventions are now emitted") and the
[JVM metrics semantic conventions](https://opentelemetry.io/docs/specs/semconv/runtime/jvm-metrics/).
Name similarity is only a fallback for metrics that list doesn't cover.

## How it detects the change

| When | Check | Catches |
|---|---|---|
| CI, before deploy | [`scripts/check_schema.py`](scripts/check_schema.py) + `mimirtool analyze dashboard` | dashboards or contract using metrics/labels the pinned agent does not emit, with the new name from the official rename list |
| Runtime, with contract | `JvmContractMetricMissing`, `JvmContractLabelMissing`, `JvmUnexpectedMetric` | a contract metric or label with no data; metric names outside the schema snapshot |
| Runtime, no contract | `JvmMetricNameAppeared`, `JvmMetricNameDisappeared`, `JvmMetricCountChanged` | any change in the set of JVM metric names compared with 10 minutes ago |

The contract ([contract/jvm-metrics.yaml](contract/jvm-metrics.yaml)) maps each signal the dashboards
need to the metric and label names that currently carry it. Dashboards and alert rules are generated
from it, so fixing a rename is a change to that one file.

```mermaid
%%{init: {"theme": "base", "themeVariables": {"fontFamily": "Arial, Helvetica, sans-serif", "fontSize": "16px", "primaryColor": "#ffffff", "primaryBorderColor": "#8c959f", "primaryTextColor": "#1f2328", "lineColor": "#6e7781", "clusterBkg": "#f6f8fa", "clusterBorder": "#d0d7de", "edgeLabelBackground": "#ffffff", "titleColor": "#57606a"}, "flowchart": {"curve": "basis", "nodeSpacing": 45, "rankSpacing": 55, "padding": 18}}}%%
flowchart TB
  contract("<b>contract/jvm-metrics.yaml</b><br/>signal → metric + label names"):::gate
  contract -->|build_dashboard.py| dash("<b>Grafana dashboards</b>"):::graf
  contract -->|build_rules.py| rules("<b>Prometheus alert rules</b>"):::prom

  subgraph ci["① CI gate · make check"]
    direction LR
    mimir("<b>mimirtool analyze</b><br/>metrics the dashboards query"):::gate
    snap("<b>snapshot</b> otel-agent-#lt;version#gt;.yaml<br/>what the agent really emits"):::gate
    check("<b>check_schema.py</b><br/>missing metric or label?<br/>→ new name, official list"):::gate
    mimir --> check
    snap --> check
  end
  dash --> mimir
  contract --> check
  env("<b>.env</b><br/>OTEL_AGENT_VERSION"):::infra --> check
  testprom("<b>Prometheus</b><br/>test environment"):::prom -->|snapshot.py| snap

  subgraph rt["Runtime · Prometheus"]
    direction LR
    r2("<b>② Contract rules</b><br/>JvmContractMetricMissing<br/>JvmContractLabelMissing<br/>JvmUnexpectedMetric"):::prom
    r3("<b>③ Change rules, no contract</b><br/>JvmMetricNameAppeared<br/>JvmMetricNameDisappeared<br/>JvmMetricCountChanged"):::prom
  end
  rules --> r2 & r3
  r2 & r3 --> drift("<b>Grafana</b><br/>JVM schema drift"):::graf

  classDef jvm fill:#eaf2ff,stroke:#2f6feb,stroke-width:2px,color:#0a3069
  classDef prom fill:#fff0eb,stroke:#e6522c,stroke-width:2px,color:#5c1a06
  classDef graf fill:#fff4e5,stroke:#f46800,stroke-width:2px,color:#5c2a00
  classDef infra fill:#ffffff,stroke:#8c959f,stroke-width:1.5px,color:#24292f
  classDef gate fill:#f3eeff,stroke:#8250df,stroke-width:2px,color:#3e1f79
  classDef ok fill:#dafbe1,stroke:#1a7f37,stroke-width:2px,color:#0f3d1c
  classDef bad fill:#ffebe9,stroke:#cf222e,stroke-width:2px,color:#6e1a1f
  classDef warn fill:#fff8c5,stroke:#bf8700,stroke-width:2px,color:#4d2d00
```

## Demo walkthrough

What happened when the agent was upgraded from 1.33.6 to 2.33.1, step by step.

```mermaid
%%{init: {"theme": "base", "themeVariables": {"fontFamily": "Arial, Helvetica, sans-serif", "fontSize": "16px", "primaryColor": "#ffffff", "primaryBorderColor": "#8c959f", "primaryTextColor": "#1f2328", "lineColor": "#6e7781", "clusterBkg": "#f6f8fa", "clusterBorder": "#d0d7de", "edgeLabelBackground": "#ffffff", "titleColor": "#57606a"}, "flowchart": {"curve": "basis", "nodeSpacing": 45, "rankSpacing": 55, "padding": 18}}}%%
flowchart TB
  subgraph row1["Break"]
    direction LR
    s1("<b>① Baseline</b><br/>agent 1.33.6<br/>dashboards + alerts green"):::ok
    s2("<b>② Bump .env to 2.33.1</b><br/>CI gate ✗<br/>no snapshot for 2.33.1"):::bad
    s3("<b>③ Deploy 2.33.1</b><br/>targets down<br/>exporter on localhost only"):::bad
    s4("<b>④ Fix exporter host</b><br/>dashboards blank<br/>24 missing · 56 unexpected<br/>18 → 14 names per service"):::bad
    s1 --> s2 --> s3 --> s4
  end
  subgraph row2["Detect and fix"]
    direction LR
    s5("<b>⑤ Snapshot 2.33.1</b><br/>CI gate ✗<br/>14 renamed metrics"):::bad
    s6("<b>⑥ Rename metrics</b><br/>CI gate ✗ 7 renamed labels<br/>16 label-missing alerts"):::warn
    s7("<b>⑦ Rename labels</b><br/>CI gate ✓<br/>dashboards back, no alerts"):::ok
    s5 --> s6 --> s7
  end
  row1 --> row2

  classDef jvm fill:#eaf2ff,stroke:#2f6feb,stroke-width:2px,color:#0a3069
  classDef prom fill:#fff0eb,stroke:#e6522c,stroke-width:2px,color:#5c1a06
  classDef graf fill:#fff4e5,stroke:#f46800,stroke-width:2px,color:#5c2a00
  classDef infra fill:#ffffff,stroke:#8c959f,stroke-width:1.5px,color:#24292f
  classDef gate fill:#f3eeff,stroke:#8250df,stroke-width:2px,color:#3e1f79
  classDef ok fill:#dafbe1,stroke:#1a7f37,stroke-width:2px,color:#0f3d1c
  classDef bad fill:#ffebe9,stroke:#cf222e,stroke-width:2px,color:#6e1a1f
  classDef warn fill:#fff8c5,stroke:#bf8700,stroke-width:2px,color:#4d2d00
```

**① Baseline, agent 1.33.6.** JVM dashboard populated, schema-drift dashboard green.

![JVM overview on agent 1.33.6](docs/images/01-jvm-overview-agent-1.x.jpg)
![Schema drift dashboard, all green](docs/images/02-schema-drift-green.jpg)

**② Bump `.env` to 2.33.1.** The CI gate refuses: nobody has recorded what 2.33.1 emits yet.

![CI gate: no snapshot for 2.33.1](docs/images/10-ci-gate-1-no-snapshot.jpg)

**③–④ Deploy 2.33.1.** First the scrape targets go down (the 2.x exporter listens on `localhost` only). After
`OTEL_EXPORTER_PROMETHEUS_HOST=0.0.0.0`, metrics flow again, but every JVM panel stops at the upgrade, with no error.
The schema alerts fire: 24 contract metrics missing, 56 unexpected metrics, 18 → 14 metric names per service.

![JVM overview after the upgrade: every line stops](docs/images/03-jvm-overview-after-upgrade.jpg)
![Schema drift dashboard after the upgrade](docs/images/04-schema-drift-red.jpg)
![Firing schema alerts in Prometheus](docs/images/05-prometheus-alerts.jpg)

**⑤ Record the 2.33.1 schema (`make snapshot`).** The CI gate now lists every renamed metric and its likely new name.

![CI gate: 14 renamed metrics](docs/images/11-ci-gate-2-renamed-metrics.jpg)

**⑥ Rename the metrics in the contract only.** Half the dashboard comes back. Heap panels stay empty because they
filter on `type`, which is now `jvm_memory_type`. The gate and the runtime alerts both point at the labels.

![CI gate: 7 renamed labels](docs/images/12-ci-gate-3-renamed-labels.jpg)
![JVM overview with metrics renamed but labels not](docs/images/06-jvm-overview-names-fixed-labels-broken.jpg)
![Schema drift dashboard: label-missing alerts](docs/images/07-schema-drift-label-missing.jpg)

**⑦ Rename the labels too.** Gate green, no schema alerts, dashboards back, including the history since the upgrade.

![CI gate green](docs/images/13-ci-gate-4-green.jpg)
![JVM overview fixed](docs/images/08-jvm-overview-fixed.jpg)
![Schema drift dashboard green again](docs/images/09-schema-drift-green-again.jpg)

## Fixing it in production

**During the incident:** roll back the agent, or keep it and make queries accept both names (`new_metric or old_metric`).
Then check that alerts on these metrics still evaluate.

```mermaid
%%{init: {"theme": "base", "themeVariables": {"fontFamily": "Arial, Helvetica, sans-serif", "fontSize": "16px", "primaryColor": "#ffffff", "primaryBorderColor": "#8c959f", "primaryTextColor": "#1f2328", "lineColor": "#6e7781", "clusterBkg": "#f6f8fa", "clusterBorder": "#d0d7de", "edgeLabelBackground": "#ffffff", "titleColor": "#57606a"}, "flowchart": {"curve": "basis", "nodeSpacing": 45, "rankSpacing": 55, "padding": 18}}}%%
flowchart TB
  start("<b>JVM panels show No data</b><br/>right after an agent upgrade"):::bad
  q{"Is the new agent<br/>needed now?"}
  rb("<b>Roll back the agent</b><br/>old names return at once"):::ok
  dual("<b>Keep the new agent</b><br/>queries accept both names<br/>new_name or old_name"):::ok
  alerts("<b>Check the alerts</b><br/>heap, GC, CPU rules<br/>still evaluate?"):::warn
  plan("<b>Plan the migration</b>"):::infra
  start --> q
  q -->|no| rb --> alerts
  q -->|yes| dual --> alerts
  alerts --> plan

  classDef jvm fill:#eaf2ff,stroke:#2f6feb,stroke-width:2px,color:#0a3069
  classDef prom fill:#fff0eb,stroke:#e6522c,stroke-width:2px,color:#5c1a06
  classDef graf fill:#fff4e5,stroke:#f46800,stroke-width:2px,color:#5c2a00
  classDef infra fill:#ffffff,stroke:#8c959f,stroke-width:1.5px,color:#24292f
  classDef gate fill:#f3eeff,stroke:#8250df,stroke-width:2px,color:#3e1f79
  classDef ok fill:#dafbe1,stroke:#1a7f37,stroke-width:2px,color:#0f3d1c
  classDef bad fill:#ffebe9,stroke:#cf222e,stroke-width:2px,color:#6e1a1f
  classDef warn fill:#fff8c5,stroke:#bf8700,stroke-width:2px,color:#4d2d00
```

**The migration:** bridge old and new names in the Collector, in Prometheus recording rules, or in the dashboards
(this repo), and roll out with a canary.

```mermaid
%%{init: {"theme": "base", "themeVariables": {"fontFamily": "Arial, Helvetica, sans-serif", "fontSize": "16px", "primaryColor": "#ffffff", "primaryBorderColor": "#8c959f", "primaryTextColor": "#1f2328", "lineColor": "#6e7781", "clusterBkg": "#f6f8fa", "clusterBorder": "#d0d7de", "edgeLabelBackground": "#ffffff", "titleColor": "#57606a"}, "flowchart": {"curve": "basis", "nodeSpacing": 45, "rankSpacing": 55, "padding": 18}}}%%
flowchart TB
  subgraph where["Where the old names can be kept alive"]
    direction LR
    app("<b>Services on agent 2.x</b><br/>send new names"):::jvm
    col("<b>A · OTel Collector</b><br/>transform processor<br/>renames new → old"):::gate
    prom("<b>Prometheus</b>"):::prom
    rec("<b>B · Recording rules</b><br/>copy new metric<br/>under the old name"):::prom
    graf("<b>Grafana</b>"):::graf
    q("<b>C · Dashboards as code</b><br/>queries use new names<br/>new or old during rollout"):::ok
    app --> col --> prom --> rec --> graf
    q -.-> graf
  end
  subgraph roll["Rollout order"]
    direction LR
    r1("<b>1</b> · Dashboards<br/>accept both names"):::infra --> r2("<b>2</b> · Upgrade one<br/>service, canary"):::infra --> r3("<b>3</b> · Verify, migrate<br/>dashboards + alerts"):::infra --> r4("<b>4</b> · Upgrade<br/>the rest"):::infra --> r5("<b>5</b> · Remove<br/>fallbacks"):::ok
  end
  where ~~~ roll

  classDef jvm fill:#eaf2ff,stroke:#2f6feb,stroke-width:2px,color:#0a3069
  classDef prom fill:#fff0eb,stroke:#e6522c,stroke-width:2px,color:#5c1a06
  classDef graf fill:#fff4e5,stroke:#f46800,stroke-width:2px,color:#5c2a00
  classDef infra fill:#ffffff,stroke:#8c959f,stroke-width:1.5px,color:#24292f
  classDef gate fill:#f3eeff,stroke:#8250df,stroke-width:2px,color:#3e1f79
  classDef ok fill:#dafbe1,stroke:#1a7f37,stroke-width:2px,color:#0f3d1c
  classDef bad fill:#ffebe9,stroke:#cf222e,stroke-width:2px,color:#6e1a1f
  classDef warn fill:#fff8c5,stroke:#bf8700,stroke-width:2px,color:#4d2d00
```

**Prevention:** pinned versions, a CI gate against a staging snapshot, a production canary, and alerts on the
monitoring itself.

```mermaid
%%{init: {"theme": "base", "themeVariables": {"fontFamily": "Arial, Helvetica, sans-serif", "fontSize": "16px", "primaryColor": "#ffffff", "primaryBorderColor": "#8c959f", "primaryTextColor": "#1f2328", "lineColor": "#6e7781", "clusterBkg": "#f6f8fa", "clusterBorder": "#d0d7de", "edgeLabelBackground": "#ffffff", "titleColor": "#57606a"}, "flowchart": {"curve": "basis", "nodeSpacing": 45, "rankSpacing": 55, "padding": 18}}}%%
flowchart TB
  subgraph ship["Every agent upgrade"]
    direction LR
    pr("<b>Renovate PR</b><br/>pinned version bump"):::infra
    notes("<b>Release notes</b><br/>renamed · breaking"):::infra
    ci("<b>CI gate</b><br/>dashboards + alerts<br/>vs new snapshot"):::gate
    stg("<b>Staging deploy</b><br/>capture snapshot"):::gate
    canary("<b>Production canary</b><br/>one service"):::gate
    all("<b>Full rollout</b>"):::ok
    pr --> notes --> ci --> stg --> canary --> all
    stg -.->|snapshot| ci
  end
  subgraph mon["Monitor the monitoring"]
    direction LR
    m1("Critical metric absent"):::prom
    m2("Set of metric names changed"):::prom
    m3("Scrape target down"):::prom
    m1 ~~~ m2 ~~~ m3
  end
  ship ~~~ mon

  classDef jvm fill:#eaf2ff,stroke:#2f6feb,stroke-width:2px,color:#0a3069
  classDef prom fill:#fff0eb,stroke:#e6522c,stroke-width:2px,color:#5c1a06
  classDef graf fill:#fff4e5,stroke:#f46800,stroke-width:2px,color:#5c2a00
  classDef infra fill:#ffffff,stroke:#8c959f,stroke-width:1.5px,color:#24292f
  classDef gate fill:#f3eeff,stroke:#8250df,stroke-width:2px,color:#3e1f79
  classDef ok fill:#dafbe1,stroke:#1a7f37,stroke-width:2px,color:#0f3d1c
  classDef bad fill:#ffebe9,stroke:#cf222e,stroke-width:2px,color:#6e1a1f
  classDef warn fill:#fff8c5,stroke:#bf8700,stroke-width:2px,color:#4d2d00
```

## Run it

Needs Docker and Python 3 with PyYAML. Host ports: app 8180, Prometheus 9190, Grafana 3100.

```bash
make up          # PetClinic + agent from .env, Prometheus, Grafana, k6 traffic,
                 # and a deploy marker (Grafana annotation) with the agent version
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
