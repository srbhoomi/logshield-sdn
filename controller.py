"""Ryu (OpenFlow 1.3) controller: L2 learning switch + dynamic prioritization of critical logs.

Modes (read every second from /tmp/prio_mode, so experiments can switch without restarting):
  off      - no prioritization (baseline: all traffic in queue 0)
  static   - critical-log rule (UDP 9999 -> queue 1) always installed
  dynamic  - rule installed only when the server-facing port is congested, removed when it clears

Run:  ryu-manager --ofp-tcp-listen-port 6653 controller.py
"""
import os
import time

from ryu.base import app_manager
from ryu.controller import ofp_event
from ryu.controller.handler import CONFIG_DISPATCHER, MAIN_DISPATCHER, DEAD_DISPATCHER, set_ev_cls
from ryu.lib import hub
from ryu.lib.packet import packet, ethernet, ether_types
from ryu.ofproto import ofproto_v1_3

SERVER_IP = "10.0.0.1"
SERVER_PORT = 1            # switch port facing h1 (s1-eth1)
CRIT_UDP = 9999
CRIT_QUEUE = 1             # OVS queue with guaranteed bandwidth (set up in run_experiment.py)
LINK_BPS = 10_000_000      # bottleneck capacity (matches the OVS HTB max-rate)
PRIO_RULE = 100            # priority of the critical-log flow rule
HIGH, LOW = 0.80, 0.40     # congestion thresholds (fraction of link capacity)
CLEAR_POLLS = 3            # consecutive low polls before removing the rule
INTERVAL = 1.0             # stats polling period (s)
MODE_FILE = "/tmp/prio_mode"
OUT_DIR = os.environ.get("CTRL_LOG_DIR", "controller_logs")


class LogPriorityController(app_manager.RyuApp):
    OFP_VERSIONS = [ofproto_v1_3.OFP_VERSION]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.mac_to_port = {}
        self.datapaths = {}
        self.prio_on = False
        self.prev = None
        self.low_count = 0
        os.makedirs(OUT_DIR, exist_ok=True)
        self.evlog = open(os.path.join(OUT_DIR, "controller_events.log"), "a", buffering=1)
        self.pcsv = open(os.path.join(OUT_DIR, "port_stats.csv"), "a", buffering=1)
        self.fcsv = open(os.path.join(OUT_DIR, "flow_stats.csv"), "a", buffering=1)
        self.pcsv.write("time,mode,tx_bps,util,prio_on\n")
        self.fcsv.write("time,priority,packets,bytes\n")
        hub.spawn(self._monitor)

    # ---------- helpers ----------
    def _log(self, msg):
        self.evlog.write(f"{time.time():.3f} {msg}\n")
        self.logger.info(msg)

    def _mode(self):
        try:
            with open(MODE_FILE) as f:
                return f.read().strip()
        except OSError:
            return "dynamic"

    def _add_flow(self, dp, priority, match, actions):
        p = dp.ofproto_parser
        inst = [p.OFPInstructionActions(dp.ofproto.OFPIT_APPLY_ACTIONS, actions)]
        dp.send_msg(p.OFPFlowMod(datapath=dp, priority=priority, match=match, instructions=inst))

    # ---------- switch connect / learning switch ----------
    @set_ev_cls(ofp_event.EventOFPStateChange, [MAIN_DISPATCHER, DEAD_DISPATCHER])
    def _state_change(self, ev):
        dp = ev.datapath
        if ev.state == MAIN_DISPATCHER:
            self.datapaths[dp.id] = dp
            self._log(f"switch {dp.id} connected")
        elif ev.state == DEAD_DISPATCHER:
            self.datapaths.pop(dp.id, None)

    @set_ev_cls(ofp_event.EventOFPSwitchFeatures, CONFIG_DISPATCHER)
    def _features(self, ev):
        dp = ev.msg.datapath
        p, o = dp.ofproto_parser, dp.ofproto
        self._add_flow(dp, 0, p.OFPMatch(), [p.OFPActionOutput(o.OFPP_CONTROLLER, o.OFPCML_NO_BUFFER)])

    @set_ev_cls(ofp_event.EventOFPPacketIn, MAIN_DISPATCHER)
    def _packet_in(self, ev):
        msg = ev.msg
        dp = msg.datapath
        o, p = dp.ofproto, dp.ofproto_parser
        in_port = msg.match["in_port"]
        eth = packet.Packet(msg.data).get_protocols(ethernet.ethernet)[0]
        if eth.ethertype == ether_types.ETH_TYPE_LLDP:
            return
        table = self.mac_to_port.setdefault(dp.id, {})
        table[eth.src] = in_port
        out_port = table.get(eth.dst, o.OFPP_FLOOD)
        actions = [p.OFPActionOutput(out_port)]
        if out_port != o.OFPP_FLOOD:
            self._add_flow(dp, 1, p.OFPMatch(in_port=in_port, eth_dst=eth.dst, eth_src=eth.src), actions)
        data = msg.data if msg.buffer_id == o.OFP_NO_BUFFER else None
        dp.send_msg(p.OFPPacketOut(datapath=dp, buffer_id=msg.buffer_id, in_port=in_port,
                                   actions=actions, data=data))

    # ---------- monitoring ----------
    def _monitor(self):
        while True:
            for dp in list(self.datapaths.values()):
                p, o = dp.ofproto_parser, dp.ofproto
                dp.send_msg(p.OFPPortStatsRequest(dp, 0, o.OFPP_ANY))
                dp.send_msg(p.OFPFlowStatsRequest(dp))
            hub.sleep(INTERVAL)

    @set_ev_cls(ofp_event.EventOFPPortStatsReply, MAIN_DISPATCHER)
    def _port_reply(self, ev):
        now = time.time()
        for st in ev.msg.body:
            if st.port_no != SERVER_PORT:
                continue
            if self.prev is not None:
                pb, pt = self.prev
                rate = (st.tx_bytes - pb) * 8 / max(now - pt, 1e-3)
                self._decide(ev.msg.datapath, rate)
            self.prev = (st.tx_bytes, now)

    @set_ev_cls(ofp_event.EventOFPFlowStatsReply, MAIN_DISPATCHER)
    def _flow_reply(self, ev):
        now = time.time()
        for st in ev.msg.body:
            if st.priority == PRIO_RULE:
                self.fcsv.write(f"{now:.3f},{st.priority},{st.packet_count},{st.byte_count}\n")

    # ---------- dynamic decision ----------
    def _decide(self, dp, rate):
        mode = self._mode()
        util = rate / LINK_BPS
        want = self.prio_on
        if mode == "off":
            want = False
        elif mode == "static":
            want = True
        else:  # dynamic
            if util >= HIGH:
                want, self.low_count = True, 0
            elif util < LOW and self.prio_on:
                self.low_count += 1
                if self.low_count >= CLEAR_POLLS:
                    want = False
            else:
                self.low_count = 0
        self.pcsv.write(f"{time.time():.3f},{mode},{rate:.0f},{util:.2f},{int(self.prio_on)}\n")
        if want != self.prio_on:
            self._set_prio(dp, want, f"mode={mode} util={util:.0%}")

    def _set_prio(self, dp, enable, reason):
        p, o = dp.ofproto_parser, dp.ofproto
        match = p.OFPMatch(eth_type=0x0800, ip_proto=17, ipv4_dst=SERVER_IP, udp_dst=CRIT_UDP)
        if enable:
            actions = [p.OFPActionSetQueue(CRIT_QUEUE), p.OFPActionOutput(SERVER_PORT)]
            self._add_flow(dp, PRIO_RULE, match, actions)
            self._log(f"CONGESTION/PRIORITY ON  -> critical UDP {CRIT_UDP} to queue {CRIT_QUEUE} ({reason})")
        else:
            dp.send_msg(p.OFPFlowMod(datapath=dp, command=o.OFPFC_DELETE_STRICT, priority=PRIO_RULE,
                                     match=match, out_port=o.OFPP_ANY, out_group=o.OFPG_ANY))
            self._log(f"PRIORITY OFF -> rule removed ({reason})")
        self.prio_on = enable
        self.low_count = 0
