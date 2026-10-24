#!/usr/bin/env python3
"""Mininet topology + OVS QoS queues + automated experiments.

Topology:   h1 (log server) --[10 Mbps bottleneck, s1-eth1]-- s1 -- h2, h3 (log clients), h4 (congestion source)

Usage (controller must already be running):
    sudo python3 run_experiment.py          # run all trials -> results/
    sudo python3 run_experiment.py --cli    # build topology + QoS and open the Mininet CLI for manual demo
"""
import os
import sys
import time

from mininet.cli import CLI
from mininet.log import setLogLevel
from mininet.net import Mininet
from mininet.node import OVSKernelSwitch, RemoteController

HERE = os.path.dirname(os.path.abspath(__file__))
RESULTS = os.path.join(HERE, "results")
MODE_FILE = "/tmp/prio_mode"

MODES = ["off", "static", "dynamic"]
LOADS = [0, 12, 25]        # Mbps of UDP background traffic (0 = no congestion)
DURATION = 30              # seconds clients send logs
RATE = 100                 # log messages/sec per client
NOISE_START = 5            # congestion starts 5 s into the run
NOISE_LEN = 20


def build():
    net = Mininet(controller=None, switch=OVSKernelSwitch, autoSetMacs=True)
    net.addController("c0", controller=RemoteController, ip="127.0.0.1", port=6653)
    s1 = net.addSwitch("s1", protocols="OpenFlow13")
    h1 = net.addHost("h1", ip="10.0.0.1/24")   # server
    h2 = net.addHost("h2", ip="10.0.0.2/24")   # log client
    h3 = net.addHost("h3", ip="10.0.0.3/24")   # log client
    h4 = net.addHost("h4", ip="10.0.0.4/24")   # congestion generator
    net.addLink(h1, s1)    # FIRST link -> s1-eth1 = port 1 (server-facing, the bottleneck)
    net.addLink(h2, s1)
    net.addLink(h3, s1)
    net.addLink(h4, s1)
    return net


def setup_qos():
    """10 Mbps HTB on the server-facing port: queue 0 = normal/default, queue 1 = critical (6 Mbps guaranteed)."""
    cmd = (
        "ovs-vsctl -- set port s1-eth1 qos=@newqos "
        "-- --id=@newqos create qos type=linux-htb other-config:max-rate=10000000 queues:0=@q0 queues:1=@q1 "
        "-- --id=@q0 create queue other-config:min-rate=1000000 other-config:max-rate=10000000 "
        "-- --id=@q1 create queue other-config:min-rate=6000000 other-config:max-rate=10000000"
    )
    os.system("ovs-vsctl --if-exists clear port s1-eth1 qos >/dev/null 2>&1")
    os.system("ovs-vsctl --all destroy qos >/dev/null 2>&1; ovs-vsctl --all destroy queue >/dev/null 2>&1")
    rc = os.system(cmd)
    if rc != 0:
        print("!! QoS setup failed - check ovs-vsctl output above")
        sys.exit(1)


def set_mode(mode):
    with open(MODE_FILE, "w") as f:
        f.write(mode)


def run_trial(net, mode, load):
    h1, h2, h3, h4 = (net.get(n) for n in ("h1", "h2", "h3", "h4"))
    tag = f"{mode}_{load}M"
    out = os.path.join(RESULTS, tag)
    os.makedirs(out, exist_ok=True)
    print(f"\n=== trial {tag} ===")
    set_mode(mode)
    time.sleep(5)  # let the controller apply the mode / clear old state

    h1.cmd(f"python3 {HERE}/server.py --out {out} --duration {DURATION + 4} > {out}/server.out 2>&1 &")
    time.sleep(1)
    for h in (h2, h3):
        h.cmd(f"python3 {HERE}/log_client.py --server 10.0.0.1 --id {h.name} --count {RATE * DURATION} "
              f"--rate {RATE} --critical-ratio 0.2 --out {out} > {out}/{h.name}.out 2>&1 &")
    if load > 0:
        time.sleep(NOISE_START)
        h4.cmd(f"iperf -u -c 10.0.0.1 -b {load}M -t {NOISE_LEN} > {out}/iperf.out 2>&1 &")
        time.sleep(DURATION + 5 - NOISE_START)
    else:
        time.sleep(DURATION + 5)
    h1.cmd("pkill -f server.py")
    # keep the controller's view in the results folder
    os.system(f"cp -r {HERE}/controller_logs {out}/ 2>/dev/null")


def main():
    setLogLevel("info")
    net = build()
    net.start()
    setup_qos()
    net.waitConnected()
    set_mode("dynamic")
    net.pingAll()   # lets the controller learn MACs

    net.get("h1").cmd("iperf -u -s > /dev/null 2>&1 &")   # sink for congestion traffic

    if "--cli" in sys.argv:
        print("\nManual demo: h1 python3 server.py --out results/demo &  |  h2 python3 log_client.py --id h2 --out results/demo")
        CLI(net)
    else:
        for load in LOADS:
            for mode in MODES:
                run_trial(net, mode, load)
        print("\nAll trials done. Now run:  python3 analyze.py")
    net.get("h1").cmd("pkill iperf")
    net.stop()


if __name__ == "__main__":
    main()
