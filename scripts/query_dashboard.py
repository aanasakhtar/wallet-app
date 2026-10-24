"""
Runs the queries of the dashboard's Part C row against a live Prometheus and prints the results.
Use it to check the panels return data, or to capture evidence next to your Grafana screenshots.

    python scripts/query_dashboard.py --prometheus http://localhost:9090 --range 10m
"""
import argparse
import json
import pathlib
import urllib.parse
import urllib.request

ap = argparse.ArgumentParser()
ap.add_argument("--prometheus", default="http://localhost:9090")
ap.add_argument("--range", default="10m", help="value substituted for Grafana's $__range")
ap.add_argument("--from-row", default="Part C")
a = ap.parse_args()

dash = json.load(open(pathlib.Path(__file__).resolve().parent.parent /
                      "observability/grafana/dashboards/wallet-app-provisioned.json"))
active = False
for p in dash["panels"]:
    if p["type"] == "row":
        active = p["title"].startswith(a.from_row)
        continue
    if not active:
        continue
    print(f"\n### {p['title']}")
    for t in p["targets"]:
        q = t["expr"].replace("$__range", a.range)
        url = f"{a.prometheus}/api/v1/query?" + urllib.parse.urlencode({"query": q})
        res = json.load(urllib.request.urlopen(url, timeout=15))
        print(f"  query: {t['expr']}")
        rows = res["data"]["result"]
        if not rows:
            print("  -> (no data)")
        for r in rows:
            labels = ",".join(f"{k}={v}" for k, v in r["metric"].items() if k not in ("__name__",))
            v = float(r["value"][1])
            print(f"  -> {{{labels}}} {v:.4g}")
