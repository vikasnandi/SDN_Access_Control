from ryu.base import app_manager
from ryu.controller import ofp_event
from ryu.controller.handler import CONFIG_DISPATCHER, MAIN_DISPATCHER, set_ev_cls
from ryu.ofproto import ofproto_v1_3
from ryu.lib.packet import packet, ethernet, arp


class AccessControl(app_manager.RyuApp):
    OFP_VERSIONS = [ofproto_v1_3.OFP_VERSION]

    def __init__(self, *args, **kwargs):
        super(AccessControl, self).__init__(*args, **kwargs)

        self.authorized_hosts = {
            "00:00:00:00:00:01",
            "00:00:00:00:00:02"
        }

        self.allowed_pairs = {
            ("00:00:00:00:00:01", "00:00:00:00:00:02"),
            ("00:00:00:00:00:02", "00:00:00:00:00:01")
        }

        self.mac_to_port = {}

    @set_ev_cls(ofp_event.EventOFPSwitchFeatures, CONFIG_DISPATCHER)
    def switch_features_handler(self, ev):
        datapath = ev.msg.datapath
        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser

        match = parser.OFPMatch()
        actions = [
            parser.OFPActionOutput(
                ofproto.OFPP_CONTROLLER,
                ofproto.OFPCML_NO_BUFFER
            )
        ]
        self.add_flow(datapath, 0, match, actions)

    def add_flow(self, datapath, priority, match, actions):
        parser = datapath.ofproto_parser
        ofproto = datapath.ofproto

        inst = [
            parser.OFPInstructionActions(
                ofproto.OFPIT_APPLY_ACTIONS,
                actions
            )
        ]

        mod = parser.OFPFlowMod(
            datapath=datapath,
            priority=priority,
            match=match,
            instructions=inst
        )
        datapath.send_msg(mod)

    @set_ev_cls(ofp_event.EventOFPPacketIn, MAIN_DISPATCHER)
    def packet_in_handler(self, ev):
        msg = ev.msg
        datapath = msg.datapath
        parser = datapath.ofproto_parser
        ofproto = datapath.ofproto
        in_port = msg.match['in_port']

        pkt = packet.Packet(msg.data)
        eth = pkt.get_protocol(ethernet.ethernet)

        if eth is None:
            return

        src = eth.src
        dst = eth.dst
        dpid = datapath.id

        self.mac_to_port.setdefault(dpid, {})
        self.mac_to_port[dpid][src] = in_port

        # Always allow ARP
        arp_pkt = pkt.get_protocol(arp.arp)
        if arp_pkt:
            out_port = self.mac_to_port[dpid].get(dst, ofproto.OFPP_FLOOD)

        else:
            # Unknown destination → flood
            if dst not in self.mac_to_port[dpid]:
                out_port = ofproto.OFPP_FLOOD

            else:
                # Block unauthorized host
                if src not in self.authorized_hosts:
                    self.logger.info("BLOCKED unauthorized source: %s", src)
                    return

                # Block unauthorized pair
                if (src, dst) not in self.allowed_pairs:
                    self.logger.info("BLOCKED pair: %s -> %s", src, dst)
                    return

                self.logger.info("ALLOWED: %s -> %s", src, dst)
                out_port = self.mac_to_port[dpid][dst]

        actions = [parser.OFPActionOutput(out_port)]

        out = parser.OFPPacketOut(
            datapath=datapath,
            buffer_id=ofproto.OFP_NO_BUFFER,
            in_port=in_port,
            actions=actions,
            data=msg.data
        )

        datapath.send_msg(out)