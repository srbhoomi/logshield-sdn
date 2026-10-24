#!/usr/bin/env python3
"""Central UDP log server.
Normal logs (INFO/WARNING/ERROR) arrive on UDP 9998, CRITICAL logs on UDP 9999.
Writes recv.csv (used by analyze.py) and aggregated.log (human-readable, by severity).

Usage: python3 server.py --out results/demo --duration 40
"""
import argparse, json, os, signal, socket, threading, time

NORMAL_PORT, CRITICAL_PORT = 9998, 9999


def serve(port, state):
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)   # avoids "Address already in use" after a crash
    s.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 8 * 1024 * 1024)
    s.bind(("0.0.0.0", port))
    s.settimeout(0.5)
    print(f"[server] listening on UDP {port}", flush=True)
    while not state["stop"].is_set():
        try:
            data, addr = s.recvfrom(4096)
        except socket.timeout:
            continue
        t_recv = time.time()
        try:
            rec = json.loads(data.decode())
            sev, client, seq, ts = rec["sev"], rec["cid"], int(rec["seq"]), float(rec["ts"])
        except (ValueError, KeyError, UnicodeDecodeError):
            with state["lock"]:
                state["bad"] += 1
            continue
        delay_ms = (t_recv - ts) * 1000.0
        with state["lock"]:
            state["count"][sev] = state["count"].get(sev, 0) + 1
            state["csv"].write(f"{client},{seq},{sev},{port},{ts:.6f},{t_recv:.6f},{delay_ms:.3f}\n")
            state["log"].write(f"{time.strftime('%H:%M:%S', time.localtime(t_recv))} [{sev}] {client} #{seq} {rec.get('msg','')[:40]}\n")
    s.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="results/manual")
    ap.add_argument("--duration", type=float, default=0, help="seconds to run (0 = until Ctrl+C)")
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)

    state = {
        "stop": threading.Event(), "lock": threading.Lock(), "bad": 0, "count": {},
        "csv": open(os.path.join(a.out, "recv.csv"), "w"),
        "log": open(os.path.join(a.out, "aggregated.log"), "w"),
    }
    state["csv"].write("client,seq,sev,port,ts_sent,ts_recv,delay_ms\n")

    def stop(*_):
        state["stop"].set()
    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)

    threads = [threading.Thread(target=serve, args=(p, state), daemon=True) for p in (NORMAL_PORT, CRITICAL_PORT)]
    for t in threads:
        t.start()

    t0 = time.time()
    while not state["stop"].is_set():
        if a.duration and time.time() - t0 >= a.duration:
            break
        time.sleep(0.2)
    state["stop"].set()
    for t in threads:
        t.join(timeout=2)
    state["csv"].close()
    state["log"].close()
    print(f"[server] done. per-severity counts: {state['count']}  malformed: {state['bad']}", flush=True)


if __name__ == "__main__":
    main()
