#!/usr/bin/env python3
"""Compute packet loss and delivery delay per trial/severity class; write summary.csv and graphs."""
import csv, glob, os, sys

RES = sys.argv[1] if len(sys.argv) > 1 else "results"
rows = []

for d in sorted(glob.glob(os.path.join(RES, "*_*M"))):
    tag = os.path.basename(d)
    mode, load = tag.rsplit("_", 1)
    load = int(load[:-1])
    sent = {}
    for f in glob.glob(os.path.join(d, "sent_*.csv")):
        client = os.path.basename(f)[5:-4]
        for r in csv.DictReader(open(f)):
            sent[(client, int(r["seq"]))] = r["sev"]
    recv = {}
    rp = os.path.join(d, "recv.csv")
    if os.path.exists(rp):
        for r in csv.DictReader(open(rp)):
            recv[(r["client"], int(r["seq"]))] = float(r["delay_ms"])
    for cls in ("CRITICAL", "NORMAL"):
        keys = [k for k, s in sent.items() if (s == "CRITICAL") == (cls == "CRITICAL")]
        delays = sorted(recv[k] for k in keys if k in recv)
        n, got = len(keys), len(delays)
        loss = 100.0 * (n - got) / n if n else 0.0
        mean = sum(delays) / got if got else float("nan")
        p95 = delays[int(0.95 * (got - 1))] if got else float("nan")
        rows.append(dict(mode=mode, load=load, cls=cls, sent=n, received=got,
                         loss_pct=round(loss, 2), mean_delay_ms=round(mean, 2), p95_delay_ms=round(p95, 2)))

if not rows:
    sys.exit("no results found - run run_experiment.py first")

print(f"{'mode':8} {'load':>5} {'class':9} {'sent':>6} {'recv':>6} {'loss%':>7} {'mean ms':>9} {'p95 ms':>9}")
for r in rows:
    print(f"{r['mode']:8} {r['load']:>4}M {r['cls']:9} {r['sent']:>6} {r['received']:>6} "
          f"{r['loss_pct']:>7} {r['mean_delay_ms']:>9} {r['p95_delay_ms']:>9}")

with open(os.path.join(RES, "summary.csv"), "w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
    w.writeheader()
    w.writerows(rows)

try:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
except ImportError:
    sys.exit("matplotlib not installed (pip3 install matplotlib) - summary.csv written, skipping graphs")

loads = sorted({r["load"] for r in rows})
for metric, ylabel, fname in (("loss_pct", "Packet loss (%)", "loss.png"),
                              ("mean_delay_ms", "Mean delivery delay (ms)", "delay.png")):
    fig, ax = plt.subplots(figsize=(7, 4.5))
    for mode in ("off", "static", "dynamic"):
        for cls, ls in (("CRITICAL", "-"), ("NORMAL", "--")):
            ys = [next((r[metric] for r in rows if r["mode"] == mode and r["load"] == l and r["cls"] == cls), None)
                  for l in loads]
            ax.plot(loads, ys, ls, marker="o", label=f"{mode} / {cls}")
    ax.set_xlabel("Background UDP load (Mbps) on 10 Mbps bottleneck")
    ax.set_ylabel(ylabel)
    ax.grid(alpha=.3)
    ax.legend(fontsize=7)
    fig.tight_layout()
    fig.savefig(os.path.join(RES, fname), dpi=150)
print("saved summary.csv, loss.png, delay.png in", RES)
