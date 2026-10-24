#!/usr/bin/env bash
# Rewrites the stack configs with absolute paths for THIS machine and writes
# a start script for each component.
#
# Needed because Filebeat does not resolve relative paths or ~, and Grafana's
# provisioning needs an absolute dashboards path.
#
# Usage:  ./scripts/configure_stack.sh

set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
STACK_DIR="$PROJECT_DIR/stack"
RUN_DIR="$PROJECT_DIR/stack/run"

if [ ! -d "$STACK_DIR" ]; then
  echo "stack/ not found. Run ./scripts/setup_stack.sh first."
  exit 1
fi

mkdir -p "$RUN_DIR"

HOSTNAME_SHORT="$(hostname -s 2>/dev/null || hostname)"
PROM_DIR="$(find "$STACK_DIR" -maxdepth 1 -type d -name 'prometheus-*' | head -1)"
NE_DIR="$(find "$STACK_DIR" -maxdepth 1 -type d -name 'node_exporter-*' | head -1)"
GRAFANA_DIR="$(find "$STACK_DIR" -maxdepth 1 -type d -name 'grafana-*' | head -1)"
ES_DIR="$(find "$STACK_DIR" -maxdepth 1 -type d -name 'elasticsearch-*' | head -1)"
KIBANA_DIR="$(find "$STACK_DIR" -maxdepth 1 -type d -name 'kibana-*' | head -1)"
FB_DIR="$(find "$STACK_DIR" -maxdepth 1 -type d -name 'filebeat-*' | head -1)"

echo "Project:  $PROJECT_DIR"
echo "Hostname: $HOSTNAME_SHORT"
echo

# --------------------------------------------------------------------------
# Prometheus: set the real machine name on the node-exporter job so the
# report can name the machine being measured (Part B requirement).
# --------------------------------------------------------------------------
echo "Configuring Prometheus..."
sed "s|nodename: dev-laptop|nodename: $HOSTNAME_SHORT|" \
  "$PROJECT_DIR/observability/prometheus/prometheus.yml" > "$RUN_DIR/prometheus.yml"
echo "  wrote $RUN_DIR/prometheus.yml (nodename=$HOSTNAME_SHORT)"

# --------------------------------------------------------------------------
# Filebeat: absolute log path.
# --------------------------------------------------------------------------
echo "Configuring Filebeat..."
sed "s|/absolute/path/to/wallet-app/logs/wallet-\*.json|$PROJECT_DIR/logs/wallet-*.json|" \
  "$PROJECT_DIR/observability/filebeat/filebeat.yml" > "$RUN_DIR/filebeat.yml"
# Filebeat refuses to start if its config is group/world writable.
chmod go-w "$RUN_DIR/filebeat.yml"
echo "  wrote $RUN_DIR/filebeat.yml (tailing $PROJECT_DIR/logs/wallet-*.json)"

# --------------------------------------------------------------------------
# Grafana: provisioning files with absolute dashboard path.
# --------------------------------------------------------------------------
echo "Configuring Grafana..."
GF_PROV="$RUN_DIR/grafana-provisioning"
mkdir -p "$GF_PROV/datasources" "$GF_PROV/dashboards"
cp "$PROJECT_DIR/observability/grafana/provisioning/datasources/datasource.yml" \
   "$GF_PROV/datasources/datasource.yml"
sed "s|path: /var/lib/grafana/dashboards|path: $PROJECT_DIR/observability/grafana/dashboards|" \
  "$PROJECT_DIR/observability/grafana/provisioning/dashboards/dashboards.yml" \
  > "$GF_PROV/dashboards/dashboards.yml"
echo "  wrote $GF_PROV (dashboards from observability/grafana/dashboards)"

# --------------------------------------------------------------------------
# Start scripts
# --------------------------------------------------------------------------
echo "Writing start scripts..."

cat > "$RUN_DIR/start-prometheus.sh" << EOF
#!/usr/bin/env bash
exec "$PROM_DIR/prometheus" \\
  --config.file="$RUN_DIR/prometheus.yml" \\
  --storage.tsdb.path="$RUN_DIR/prometheus-data" \\
  --storage.tsdb.retention.time=15d \\
  --web.enable-lifecycle
EOF

cat > "$RUN_DIR/start-node-exporter.sh" << EOF
#!/usr/bin/env bash
exec "$NE_DIR/node_exporter"
EOF

cat > "$RUN_DIR/start-grafana.sh" << EOF
#!/usr/bin/env bash
cd "$GRAFANA_DIR"
exec ./bin/grafana server \\
  --homepath "$GRAFANA_DIR" \\
  cfg:default.paths.provisioning="$GF_PROV" \\
  cfg:default.paths.data="$RUN_DIR/grafana-data" \\
  cfg:default.paths.logs="$RUN_DIR/grafana-logs"
EOF

cat > "$RUN_DIR/start-elasticsearch.sh" << EOF
#!/usr/bin/env bash
# Single node, security off - fine for a local assignment, never for production.
export ES_JAVA_OPTS="-Xms512m -Xmx512m"
exec "$ES_DIR/bin/elasticsearch" \\
  -E discovery.type=single-node \\
  -E xpack.security.enabled=false \\
  -E xpack.security.enrollment.enabled=false
EOF

cat > "$RUN_DIR/start-kibana.sh" << EOF
#!/usr/bin/env bash
exec "$KIBANA_DIR/bin/kibana" --elasticsearch.hosts=http://localhost:9200
EOF

cat > "$RUN_DIR/start-filebeat.sh" << EOF
#!/usr/bin/env bash
cd "$FB_DIR"
exec ./filebeat -e -strict.perms=false \\
  -c "$RUN_DIR/filebeat.yml" \\
  --path.home "$FB_DIR" \\
  --path.data "$RUN_DIR/filebeat-data" \\
  --path.logs "$RUN_DIR/filebeat-logs"
EOF

cat > "$RUN_DIR/start-app.sh" << EOF
#!/usr/bin/env bash
cd "$PROJECT_DIR"
export ADMIN_TOKEN="\${ADMIN_TOKEN:-local-admin-token}"
export DATABASE_URL="\${DATABASE_URL:-sqlite:///./wallet.db}"
exec python3 -m uvicorn app.main:app --host 127.0.0.1 --port 8000
EOF

cat > "$RUN_DIR/start-cardinality-demo.sh" << EOF
#!/usr/bin/env bash
cd "$PROJECT_DIR"
export CARDINALITY_MODE="\${CARDINALITY_MODE:-on}"
exec python3 cardinality_demo.py
EOF

chmod +x "$RUN_DIR"/*.sh

echo
echo "Done. Start each component in its own terminal:"
echo "  stack/run/start-app.sh"
echo "  stack/run/start-node-exporter.sh"
echo "  stack/run/start-prometheus.sh"
echo "  stack/run/start-grafana.sh"
echo "  stack/run/start-elasticsearch.sh   (wait for it to be ready)"
echo "  stack/run/start-kibana.sh"
echo "  stack/run/start-filebeat.sh        (start LAST, after the app has logged something)"
echo
echo "URLs:  app :8000  prometheus :9090  grafana :3000 (admin/admin)"
echo "       elasticsearch :9200  kibana :5601"
