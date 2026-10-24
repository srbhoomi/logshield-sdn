# Distributed Log Aggregation System (UDP + Mininet + SDN)

CN Project 10. UDP log clients on h2/h3 send severity-tagged, timestamped, sequence-numbered logs to a central
server on h1. A Ryu (OpenFlow 1.3) controller monitors the server-facing port and, when it is congested,
dynamically installs a flow rule that places CRITICAL logs (UDP 9999) in a guaranteed-bandwidth OVS queue.

## Architecture
```
 h2 (client) --\
 h3 (client) ---+-- s1 (OVS, OF1.3) --[s1-eth1: 10 Mbps HTB, queue0 normal / queue1 critical]-- h1 (log server)
 h4 (iperf UDP noise) -/                      |
                                        Ryu controller (port + flow stats every 1 s)
```
- Normal logs (INFO/WARNING/ERROR): UDP 9998 -> default queue 0
- CRITICAL logs: UDP 9999 -> queue 1 (6 Mbps guaranteed) once the controller installs the rule
- Controller modes (switched via /tmp/prio_mode): `off` (baseline), `static`, `dynamic` (proposed)
- Dynamic logic: tx rate on s1-eth1 >= 80% of 10 Mbps -> install rule; < 40% for 3 polls -> remove rule

## Files
| File | Purpose |
|---|---|
| server.py | UDP log server (2 ports), CSV + severity-categorised log |
| log_client.py | UDP log client with severity, timestamp, sequence number |
| controller.py | Ryu controller: learning switch, flow/port stats, dynamic priority rule |
| run_experiment.py | Mininet topology, OVS QoS queues, automated trials |
| analyze.py | Loss / delay computation, summary.csv, loss.png, delay.png |

## Setup (Ubuntu 20.04/22.04)
```
sudo apt install mininet openvswitch-switch iperf python3-pip -y
pip3 install ryu eventlet==0.30.2 matplotlib
```

## Run
Terminal 1 (controller):
```
cd log-aggregation
sudo mn -c
ryu-manager --ofp-tcp-listen-port 6653 controller.py
```
Terminal 2 (experiments, ~7 minutes):
```
sudo python3 run_experiment.py
python3 analyze.py
```
Manual demo: `sudo python3 run_experiment.py --cli`, then in the Mininet CLI:
```
h1 python3 server.py --out results/demo --duration 60 &
h2 python3 log_client.py --id h2 --count 3000 --out results/demo &
h4 iperf -u -c 10.0.0.1 -b 25M -t 20 &
```
and watch `controller_logs/controller_events.log` for the priority ON/OFF events.

## Test scenarios
- Load 0 Mbps: no congestion (functional test, expect ~0% loss).
- Load 12 Mbps: moderate congestion; 25 Mbps: heavy congestion (2.5x link capacity).
- Each load run with prioritization off / static / dynamic. Congestion starts 5 s into each 30 s run, so the
  dynamic mode's reaction time shows up as a short loss window for critical logs.

## Metrics
- Packet loss = (sent - received) / sent per class, using client sequence numbers
- Delivery time = server receive time - client send timestamp (all hosts share the VM clock)
- Baseline = prioritization off; compared against static and dynamic.

## Results
(Paste the table printed by `analyze.py` and the graphs `results/loss.png`, `results/delay.png` here, with a short discussion.)

## Troubleshooting
- `OSError: [Errno 98] Address already in use`: a previous server.py is still running. Run `sudo pkill -f server.py` and `sudo mn -c`.
- Switch not connecting: make sure ryu-manager is running first on port 6653.
