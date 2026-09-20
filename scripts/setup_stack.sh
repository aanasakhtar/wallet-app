#!/usr/bin/env bash
# Downloads the observability stack as plain binaries - no Docker required.
#
# Everything lands in ./stack/ which is gitignored. Nothing is installed
# system-wide, so cleanup is `rm -rf stack/`.
#
# Usage:  ./scripts/setup_stack.sh
#
# Linux x86_64 and macOS (Intel/Apple Silicon) are handled. On Windows, use
# WSL2 and run this script inside it - node_exporter has no Windows build
# (windows_exporter is the separate equivalent).

set -euo pipefail

PROM_VERSION="2.54.1"
NODE_EXPORTER_VERSION="1.8.2"
GRAFANA_VERSION="11.2.0"
ELASTIC_VERSION="8.15.0"

STACK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/stack"
mkdir -p "$STACK_DIR"
cd "$STACK_DIR"

OS="$(uname -s)"
ARCH="$(uname -m)"

case "$OS" in
  Linux)  PLATFORM="linux" ;;
  Darwin) PLATFORM="darwin" ;;
  *) echo "Unsupported OS: $OS. On Windows, run this inside WSL2."; exit 1 ;;
esac

case "$ARCH" in
  x86_64|amd64) GOARCH="amd64"; ES_ARCH="x86_64" ;;
  arm64|aarch64) GOARCH="arm64"; ES_ARCH="aarch64" ;;
  *) echo "Unsupported architecture: $ARCH"; exit 1 ;;
esac

echo "Platform: $PLATFORM/$GOARCH"
echo "Downloading into: $STACK_DIR"
echo

fetch() {
  local url="$1" out="$2"
  if [ -e "$out" ]; then
    echo "  already present: $out"
    return
  fi
  echo "  downloading $(basename "$url")"
  curl -fsSL --retry 3 -o "$out" "$url"
}

extract() {
  local archive="$1" expected_dir="$2"
  if [ -d "$expected_dir" ]; then
    echo "  already extracted: $expected_dir"
    return
  fi
  echo "  extracting $(basename "$archive")"
  tar -xzf "$archive"
}

# --------------------------------------------------------------------------
echo "[1/5] Prometheus"
PROM_TAR="prometheus-${PROM_VERSION}.${PLATFORM}-${GOARCH}.tar.gz"
fetch "https://github.com/prometheus/prometheus/releases/download/v${PROM_VERSION}/${PROM_TAR}" "$PROM_TAR"
extract "$PROM_TAR" "prometheus-${PROM_VERSION}.${PLATFORM}-${GOARCH}"

echo "[2/5] Node Exporter"
if [ "$PLATFORM" = "darwin" ]; then
  echo "  NOTE: node_exporter's darwin build reports far fewer metrics than"
  echo "  the linux build (no node_cpu_seconds_total breakdown by mode)."
  echo "  Some host panels will be empty on macOS - say so in your report."
fi
NE_TAR="node_exporter-${NODE_EXPORTER_VERSION}.${PLATFORM}-${GOARCH}.tar.gz"
fetch "https://github.com/prometheus/node_exporter/releases/download/v${NODE_EXPORTER_VERSION}/${NE_TAR}" "$NE_TAR"
extract "$NE_TAR" "node_exporter-${NODE_EXPORTER_VERSION}.${PLATFORM}-${GOARCH}"

echo "[3/5] Grafana"
GRAFANA_TAR="grafana-${GRAFANA_VERSION}.${PLATFORM}-${GOARCH}.tar.gz"
fetch "https://dl.grafana.com/oss/release/${GRAFANA_TAR}" "$GRAFANA_TAR"
extract "$GRAFANA_TAR" "grafana-v${GRAFANA_VERSION}"

echo "[4/5] Elasticsearch + Kibana"
ES_TAR="elasticsearch-${ELASTIC_VERSION}-${PLATFORM}-${ES_ARCH}.tar.gz"
fetch "https://artifacts.elastic.co/downloads/elasticsearch/${ES_TAR}" "$ES_TAR"
extract "$ES_TAR" "elasticsearch-${ELASTIC_VERSION}"

KIBANA_TAR="kibana-${ELASTIC_VERSION}-${PLATFORM}-${ES_ARCH}.tar.gz"
fetch "https://artifacts.elastic.co/downloads/kibana/${KIBANA_TAR}" "$KIBANA_TAR"
extract "$KIBANA_TAR" "kibana-${ELASTIC_VERSION}"

echo "[5/5] Filebeat"
FB_TAR="filebeat-${ELASTIC_VERSION}-${PLATFORM}-${ES_ARCH}.tar.gz"
fetch "https://artifacts.elastic.co/downloads/beats/filebeat/${FB_TAR}" "$FB_TAR"
extract "$FB_TAR" "filebeat-${ELASTIC_VERSION}-${PLATFORM}-${ES_ARCH}"

echo
echo "Done. Next: ./scripts/configure_stack.sh"
echo "That step rewrites the config files with absolute paths for this machine."
