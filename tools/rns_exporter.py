#!/usr/bin/env python3
"""
Prometheus metrics exporter for Reticulum transport nodes.
Connects to running rnsd via shared instance RPC — does NOT start its own transport.

Usage: python3 rns_exporter.py [--port 9150] [--interval 15]
"""

import time
import threading
import argparse
from http.server import HTTPServer, BaseHTTPRequestHandler

import RNS

METRICS = {}
LOCK = threading.Lock()


def collect_metrics(reticulum):
    """Collect metrics from running Reticulum shared instance."""
    global METRICS
    try:
        stats = reticulum.get_interface_stats()
        if not stats:
            return

        m = {}
        m["rns_uptime_seconds"] = stats.get("transport_uptime", 0) or 0
        m["rns_interfaces_total"] = len(stats.get("interfaces", []))
        m["rns_traffic_rx_bytes"] = stats.get("rxb", 0)
        m["rns_traffic_tx_bytes"] = stats.get("txb", 0)
        m["rns_traffic_rx_bps"] = stats.get("rxs", 0)
        m["rns_traffic_tx_bps"] = stats.get("txs", 0)
        m["rns_rss_bytes"] = stats.get("rss", 0) or 0

        # Path table size via RPC
        try:
            path_table = reticulum.get_path_table()
            m["rns_path_table_size"] = len(path_table) if path_table else 0
        except:
            m["rns_path_table_size"] = 0

        try:
            m["rns_link_count"] = reticulum.get_link_count() or 0
        except:
            m["rns_link_count"] = 0

        # Announce rate table size
        try:
            rate_table = reticulum.get_rate_table()
            m["rns_announce_rate_table_size"] = len(rate_table) if rate_table else 0
        except:
            m["rns_announce_rate_table_size"] = 0

        # Blackholed identities count
        try:
            blackholed = reticulum.get_blackholed_identities()
            m["rns_blackholed_identities"] = len(blackholed) if blackholed else 0
        except:
            m["rns_blackholed_identities"] = 0

        # Per-interface metrics
        if_metrics = []
        for iface in stats.get("interfaces", []):
            name = iface.get("short_name", "unknown")
            if_metrics.append({
                "name": name,
                "type": iface.get("type", "unknown"),
                "online": 1 if iface.get("status") else 0,
                "rx_bytes": iface.get("rxb", 0),
                "tx_bytes": iface.get("txb", 0),
                "rx_bps": iface.get("rxs", 0),
                "tx_bps": iface.get("txs", 0),
                "clients": iface.get("clients") if iface.get("clients") is not None else 0,
                "announce_queue": iface.get("announce_queue") or 0,
                "held_announces": iface.get("held_announces", 0),
                "mode": iface.get("mode", 0),
                "incoming_announce_freq": iface.get("incoming_announce_frequency", 0) or 0,
                "outgoing_announce_freq": iface.get("outgoing_announce_frequency", 0) or 0,
            })

        m["interfaces"] = if_metrics

        with LOCK:
            METRICS = m

    except Exception as e:
        print(f"[rns_exporter] Error collecting metrics: {e}")


def format_prometheus():
    """Format collected metrics as Prometheus exposition text."""
    with LOCK:
        m = METRICS.copy()

    if not m:
        return "# No metrics collected yet\n"

    lines = []

    scalars = [
        ("rns_uptime_seconds", "gauge", "Transport uptime in seconds"),
        ("rns_interfaces_total", "gauge", "Number of interfaces"),
        ("rns_traffic_rx_bytes", "counter", "Total bytes received"),
        ("rns_traffic_tx_bytes", "counter", "Total bytes transmitted"),
        ("rns_traffic_rx_bps", "gauge", "Current receive rate in bits per second"),
        ("rns_traffic_tx_bps", "gauge", "Current transmit rate in bits per second"),
        ("rns_rss_bytes", "gauge", "Resident memory size in bytes"),
        ("rns_path_table_size", "gauge", "Number of entries in path table"),
        ("rns_link_count", "gauge", "Active link count"),
        ("rns_announce_rate_table_size", "gauge", "Number of entries in announce rate table"),
        ("rns_blackholed_identities", "gauge", "Number of blackholed identities"),
    ]

    for name, mtype, help_text in scalars:
        if name in m:
            lines.append(f"# HELP {name} {help_text}")
            lines.append(f"# TYPE {name} {mtype}")
            lines.append(f"{name} {m[name]}")

    if "interfaces" in m:
        for prefix, mtype, help_text, key in [
            ("rns_interface_online", "gauge", "Interface online status", "online"),
            ("rns_interface_rx_bytes", "counter", "Interface bytes received", "rx_bytes"),
            ("rns_interface_tx_bytes", "counter", "Interface bytes transmitted", "tx_bytes"),
            ("rns_interface_rx_bps", "gauge", "Interface receive rate bps", "rx_bps"),
            ("rns_interface_tx_bps", "gauge", "Interface transmit rate bps", "tx_bps"),
            ("rns_interface_clients", "gauge", "Interface connected clients", "clients"),
            ("rns_interface_announce_queue", "gauge", "Interface announce queue length", "announce_queue"),
            ("rns_interface_held_announces", "gauge", "Interface held announces", "held_announces"),
            ("rns_interface_incoming_announce_freq", "gauge", "Interface incoming announce frequency (Hz)", "incoming_announce_freq"),
            ("rns_interface_outgoing_announce_freq", "gauge", "Interface outgoing announce frequency (Hz)", "outgoing_announce_freq"),
        ]:
            lines.append(f"# HELP {prefix} {help_text}")
            lines.append(f"# TYPE {prefix} {mtype}")
            for iface in m["interfaces"]:
                name = iface["name"].replace('"', '\\"')
                itype = iface["type"]
                val = iface.get(key, 0)
                lines.append(f'{prefix}{{name="{name}",type="{itype}"}} {val}')

    return "\n".join(lines) + "\n"


class MetricsHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/metrics":
            body = format_prometheus().encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/health":
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(b"ok\n")
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format, *args):
        pass


def collector_loop(reticulum, interval):
    while True:
        collect_metrics(reticulum)
        time.sleep(interval)


def main():
    parser = argparse.ArgumentParser(description="Reticulum Prometheus Exporter")
    parser.add_argument("--port", type=int, default=9150, help="HTTP port (default: 9150)")
    parser.add_argument("--interval", type=int, default=15, help="Collection interval seconds (default: 15)")
    parser.add_argument("--rnsconfig", type=str, default=None, help="Reticulum config dir")
    args = parser.parse_args()

    print(f"[rns_exporter] Connecting to shared Reticulum instance...")
    reticulum = RNS.Reticulum(configdir=args.rnsconfig, require_shared_instance=True)
    print(f"[rns_exporter] Connected. Starting collector (interval={args.interval}s)")

    thread = threading.Thread(target=collector_loop, args=(reticulum, args.interval), daemon=True)
    thread.start()

    server = HTTPServer(("0.0.0.0", args.port), MetricsHandler)
    print(f"[rns_exporter] Listening on :{args.port}/metrics")
    server.serve_forever()


if __name__ == "__main__":
    main()
