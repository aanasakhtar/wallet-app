#!/usr/bin/env python3
"""
Downloads and configures the native Windows observability stack for the wallet app.
No Docker required. Everything lands in ./stack/ (gitignored).

Usage:
  python scripts/setup_windows_stack.py                # Setup Prometheus, Grafana, Windows Exporter
  python scripts/setup_windows_stack.py --with-elk    # Also include Filebeat, Elasticsearch, Kibana
"""

import argparse
import os
import shutil
import socket
import sys
import urllib.request
import zipfile
from pathlib import Path

# Tool versions
PROM_VERSION = "2.54.1"
GRAFANA_VERSION = "11.2.0"
WIN_EXPORTER_VERSION = "0.27.2"
ELASTIC_VERSION = "8.15.0"

DOWNLOADS = {
    "prometheus": {
        "url": f"https://github.com/prometheus/prometheus/releases/download/v{PROM_VERSION}/prometheus-{PROM_VERSION}.windows-amd64.zip",
        "archive": f"prometheus-{PROM_VERSION}.windows-amd64.zip",
        "extract_dir": f"prometheus-{PROM_VERSION}.windows-amd64",
    },
    "grafana": {
        "url": f"https://dl.grafana.com/oss/release/grafana-{GRAFANA_VERSION}.windows-amd64.zip",
        "archive": f"grafana-{GRAFANA_VERSION}.windows-amd64.zip",
        "extract_dir": f"grafana-v{GRAFANA_VERSION}",
    },
    "windows_exporter": {
        "url": f"https://github.com/prometheus-community/windows_exporter/releases/download/v{WIN_EXPORTER_VERSION}/windows_exporter-{WIN_EXPORTER_VERSION}-amd64.exe",
        "archive": "windows_exporter.exe",
        "is_direct_exe": True,
    },
}

ELK_DOWNLOADS = {
    "filebeat": {
        "url": f"https://artifacts.elastic.co/downloads/beats/filebeat/filebeat-{ELASTIC_VERSION}-windows-x86_64.zip",
        "archive": f"filebeat-{ELASTIC_VERSION}-windows-x86_64.zip",
        "extract_dir": f"filebeat-{ELASTIC_VERSION}-windows-x86_64",
    },
    "elasticsearch": {
        "url": f"https://artifacts.elastic.co/downloads/elasticsearch/elasticsearch-{ELASTIC_VERSION}-windows-x86_64.zip",
        "archive": f"elasticsearch-{ELASTIC_VERSION}-windows-x86_64.zip",
        "extract_dir": f"elasticsearch-{ELASTIC_VERSION}",
    },
    "kibana": {
        "url": f"https://artifacts.elastic.co/downloads/kibana/kibana-{ELASTIC_VERSION}-windows-x86_64.zip",
        "archive": f"kibana-{ELASTIC_VERSION}-windows-x86_64.zip",
        "extract_dir": f"kibana-{ELASTIC_VERSION}",
    },
}


def download_with_progress(url: str, dest_path: Path):
    if dest_path.exists():
        print(f"  [already downloaded] {dest_path.name}")
        return

    print(f"  Downloading {dest_path.name} from:\n    {url}")
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})

    with urllib.request.urlopen(req) as resp, open(dest_path, "wb") as out:
        total_length = resp.headers.get("content-length")
        total = int(total_length) if total_length else None
        downloaded = 0
        chunk_size = 1024 * 1024  # 1MB chunks
        last_percent = -1

        while True:
            chunk = resp.read(chunk_size)
            if not chunk:
                break
            out.write(chunk)
            downloaded += len(chunk)
            if total:
                percent = int(downloaded * 100 / total)
                if percent != last_percent and percent % 10 == 0:
                    last_percent = percent
                    mb_downloaded = downloaded / (1024 * 1024)
                    mb_total = total / (1024 * 1024)
                    print(f"    -> {percent}% ({mb_downloaded:.1f} MB / {mb_total:.1f} MB)")
            else:
                mb_downloaded = downloaded / (1024 * 1024)
                print(f"    -> {mb_downloaded:.1f} MB downloaded", end="\r")

    print(f"  [complete] {dest_path.name}")


def extract_zip(archive_path: Path, extract_to: Path):
    print(f"  Extracting {archive_path.name} ...")
    with zipfile.ZipFile(archive_path, "r") as z:
        z.extractall(extract_to)
    print(f"  [extracted] {archive_path.name}")


def configure_stack(project_dir: Path, stack_dir: Path, run_dir: Path):
    print("\n--- Configuring Windows Stack ---")
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "grafana-data").mkdir(parents=True, exist_ok=True)
    (run_dir / "grafana-logs").mkdir(parents=True, exist_ok=True)
    (run_dir / "prometheus-data").mkdir(parents=True, exist_ok=True)
    hostname = socket.gethostname()

    # Locate extracted directories
    prom_dirs = [d for d in stack_dir.glob("prometheus-*windows*") if d.is_dir()]
    prom_dir = prom_dirs[0] if prom_dirs else stack_dir / f"prometheus-{PROM_VERSION}.windows-amd64"

    grafana_dirs = [d for d in stack_dir.glob("grafana-*") if d.is_dir()]
    grafana_dir = grafana_dirs[0] if grafana_dirs else stack_dir / f"grafana-v{GRAFANA_VERSION}"

    win_exporter_exe = stack_dir / "windows_exporter.exe"

    python_exe = project_dir / ".venv" / "Scripts" / "python.exe"
    if not python_exe.exists():
        python_exe = Path(sys.executable)

    # 1. Prometheus config
    prom_src = project_dir / "observability" / "prometheus" / "prometheus.yml"
    with open(prom_src, "r", encoding="utf-8") as f:
        prom_content = f.read()

    prom_content = prom_content.replace("nodename: dev-laptop", f"nodename: {hostname}")
    prom_cfg_dest = run_dir / "prometheus.yml"
    with open(prom_cfg_dest, "w", encoding="utf-8") as f:
        f.write(prom_content)
    print(f"  Wrote Prometheus config: {prom_cfg_dest} (nodename={hostname})")

    # 2. Grafana provisioning
    gf_prov = run_dir / "grafana-provisioning"
    gf_datasources = gf_prov / "datasources"
    gf_dashboards = gf_prov / "dashboards"
    gf_datasources.mkdir(parents=True, exist_ok=True)
    gf_dashboards.mkdir(parents=True, exist_ok=True)
    (gf_prov / "plugins").mkdir(parents=True, exist_ok=True)
    (gf_prov / "alerting").mkdir(parents=True, exist_ok=True)

    # Copy datasource.yml
    shutil.copy(
        project_dir / "observability" / "grafana" / "provisioning" / "datasources" / "datasource.yml",
        gf_datasources / "datasource.yml",
    )

    # Dedicated dashboards directory with only the provisioned JSON
    gf_dashboards_dir = run_dir / "grafana-dashboards"
    gf_dashboards_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy(
        project_dir / "observability" / "grafana" / "dashboards" / "wallet-app-provisioned.json",
        gf_dashboards_dir / "wallet-app-provisioned.json",
    )

    (grafana_dir / "data" / "plugins").mkdir(parents=True, exist_ok=True)

    dashboards_path = gf_dashboards_dir.as_posix()
    dashboards_yml_content = f"""apiVersion: 1
providers:
  - name: wallet-dashboards
    orgId: 1
    folder: Wallet App
    type: file
    disableDeletion: false
    updateIntervalSeconds: 10
    allowUiUpdates: true
    options:
      path: "{dashboards_path}"
      foldersFromFilesStructure: false
"""
    with open(gf_dashboards / "dashboards.yml", "w", encoding="utf-8") as f:
        f.write(dashboards_yml_content)
    print(f"  Wrote Grafana provisioning: {gf_prov}")

    # 3. Generate Start Scripts (.bat and .ps1)
    # App
    with open(run_dir / "start-app.bat", "w", encoding="utf-8") as f:
        f.write(f'@echo off\ncd /d "{project_dir}"\n"{python_exe}" -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload\n')

    # Prometheus
    prom_exe = prom_dir / "prometheus.exe"
    with open(run_dir / "start-prometheus.bat", "w", encoding="utf-8") as f:
        f.write(f'@echo off\ncd /d "{run_dir}"\n"{prom_exe}" --config.file="{prom_cfg_dest}" --storage.tsdb.path="{run_dir / "prometheus-data"}" --storage.tsdb.retention.time=15d --web.enable-lifecycle\n')

    # Grafana
    grafana_bin = grafana_dir / "bin" / "grafana-server.exe"
    with open(run_dir / "start-grafana.bat", "w", encoding="utf-8") as f:
        f.write(f'@echo off\ncd /d "{grafana_dir}"\n"{grafana_bin}" --homepath="{grafana_dir}" cfg:default.paths.provisioning="{gf_prov}" cfg:default.paths.data="{run_dir / "grafana-data"}" cfg:default.paths.logs="{run_dir / "grafana-logs"}"\n')

    # Windows Exporter
    with open(run_dir / "start-windows-exporter.bat", "w", encoding="utf-8") as f:
        f.write(f'@echo off\n"{win_exporter_exe}" --web.listen-address=":9100"\n')

    # Cardinality Demo
    with open(run_dir / "start-cardinality-demo.bat", "w", encoding="utf-8") as f:
        f.write(f'@echo off\ncd /d "{project_dir}"\nset CARDINALITY_MODE=on\n"{python_exe}" cardinality_demo.py\n')

    print("  Generated startup scripts in stack/run/:")
    print("    - start-app.bat")
    print("    - start-prometheus.bat")
    print("    - start-grafana.bat")
    print("    - start-windows-exporter.bat")
    print("    - start-cardinality-demo.bat")


def main():
    parser = argparse.ArgumentParser(description="Setup native Windows observability stack")
    parser.add_argument("--with-elk", action="store_true", help="Also download Filebeat, Elasticsearch, Kibana")
    args = parser.parse_args()

    project_dir = Path(__file__).resolve().parent.parent
    stack_dir = project_dir / "stack"
    run_dir = stack_dir / "run"
    stack_dir.mkdir(parents=True, exist_ok=True)

    items_to_download = dict(DOWNLOADS)
    if args.with_elk:
        items_to_download.update(ELK_DOWNLOADS)

    print(f"=== Setting up Windows Observability Stack in {stack_dir} ===")

    for name, info in items_to_download.items():
        print(f"\nProcessing {name}...")
        archive_path = stack_dir / info["archive"]
        download_with_progress(info["url"], archive_path)

        if not info.get("is_direct_exe"):
            extract_dir_name = info.get("extract_dir")
            target_extract_dir = stack_dir / extract_dir_name if extract_dir_name else stack_dir
            if not target_extract_dir.exists():
                extract_zip(archive_path, stack_dir)
            else:
                print(f"  [already extracted] {target_extract_dir.name}")

    configure_stack(project_dir, stack_dir, run_dir)
    print("\n=== Setup Completed Successfully! ===")


if __name__ == "__main__":
    main()
