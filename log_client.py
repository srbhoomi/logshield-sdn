#!/usr/bin/env python3
"""UDP log client. Sends JSON logs with severity, timestamp and sequence number.
CRITICAL -> UDP 9999 (prioritized by SDN controller), everything else -> UDP 9998.

Usage: python3 log_client.py --server 10.0.0.1 --id h2 --count 3000 --rate 100 --out results/demo
"""
import argparse, json, os, random, socket, time

NORMAL_PORT, CRITICAL_PORT = 9998, 9999
PAD = "x" * 300  # make packets ~400 bytes so they generate measurable load


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--server", default="10.0.0.1")
    ap.add_argument("--id", default="h2")
    ap.add_argument("--count", type=int, default=1000)
    ap.add_argument("--rate", type=float, default=100, help="messages per second")
    ap.add_argument("--critical-ratio", type=float, default=0.2)
    ap.add_argument("--out", default="results/manual")
    ap.add_argument("--seed", type=int, default=1)
    a = ap.parse_args()

    random.seed(a.seed + hash(a.id) % 1000)
    os.makedirs(a.out, exist_ok=True)
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    f = open(os.path.join(a.out, f"sent_{a.id}.csv"), "w")
    f.write("seq,sev,port,ts_sent\n")

    start = time.time()
    for seq in range(a.count):
        r = random.random()
        if r < a.critical_ratio:
            sev, port = "CRITICAL", CRITICAL_PORT
        else:
            sev = random.choices(["INFO", "WARNING", "ERROR"], weights=[60, 25, 15])[0]
            port = NORMAL_PORT
        ts = time.time()
        msg = json.dumps({"cid": a.id, "seq": seq, "sev": sev, "ts": ts, "msg": f"{sev} event {seq} from {a.id} {PAD}"})
        try:
            sock.sendto(msg.encode(), (a.server, port))
        except OSError as e:
            print("send error:", e)
        f.write(f"{seq},{sev},{port},{ts:.6f}\n")
        # pace to the requested rate
        delay = start + (seq + 1) / a.rate - time.time()
        if delay > 0:
            time.sleep(delay)
    f.close()
    print(f"[client {a.id}] sent {a.count} logs")


if __name__ == "__main__":
    main()
