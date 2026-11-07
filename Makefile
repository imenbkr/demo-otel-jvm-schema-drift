MIMIRTOOL = docker run --rm -u $(shell id -u) -v $(CURDIR):/w -w /tmp grafana/mimirtool:2.14.0
PROMTOOL  = docker run --rm -v $(CURDIR)/prometheus:/p:ro --entrypoint promtool prom/prometheus:v3.5.0

.PHONY: up down generate snapshot check reload

AGENT_VERSION = $(shell sed -n 's/^OTEL_AGENT_VERSION=//p' .env)

up:            ## start the stack (agent version from .env) and mark the deploy in Grafana
	docker compose up -d
	@until curl -sf localhost:3100/api/health >/dev/null; do sleep 2; done
	@curl -sf -X POST localhost:3100/api/annotations -H 'Content-Type: application/json' \
	  -d '{"tags":["deploy"],"text":"Deploy: OTel Java agent $(AGENT_VERSION)"}' >/dev/null && \
	  echo "annotated deploy of agent $(AGENT_VERSION)"

down:          ## stop the stack and delete its data
	docker compose down -v

generate:      ## rebuild alert rules and dashboards from the contract
	python3 scripts/build_rules.py
	python3 scripts/build_dashboard.py

snapshot:      ## record the JVM metric schema the running agent emits
	python3 scripts/snapshot.py

check:         ## the CI gate: dashboards + contract vs the pinned agent's schema
	$(MIMIRTOOL) analyze dashboard --output=/w/.metrics-in-grafana.json /w/grafana/dashboards/jvm-overview.json /w/grafana/dashboards/jvm-schema-drift.json >/dev/null
	$(PROMTOOL) check rules /p/rules/jvm-schema.yml
	python3 scripts/check_schema.py --dashboard-metrics .metrics-in-grafana.json

reload:        ## make Prometheus re-read the rules
	curl -s -X POST localhost:9190/-/reload
