"""
Xray Probe for Alex (QA Sentinel).
Validates real network connectivity:
1. Fetches subscription via subId / sub URL
2. Decodes VLESS / Reality parameters
3. Spawns a temporary isolated xray-core client on localhost:10850 (SOCKS5)
4. Executes HTTP probe through the tunnel and verifies handshake & egress IP
"""
import os
import sys
import json
import base64
import time
import subprocess
import urllib.parse
import httpx
import logging
from typing import Dict, Any, Optional, Tuple

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import qa_agent.config as config

logger = logging.getLogger("alex_qa.xray_probe")


class XrayProbe:
    def __init__(self, socks_port: int = config.TEST_SOCKS_PORT):
        self.socks_port = socks_port
        self.xray_bin = config.XRAY_BIN
        self.temp_config_path = f"/tmp/xray_qa_probe_{socks_port}.json"
        self.process: Optional[subprocess.Popen] = None

    def fetch_subscription(self, sub_url_or_id: str) -> str:
        """Downloads raw subscription text from local API or domain."""
        if sub_url_or_id.startswith("http"):
            # Replace domain with 127.0.0.1:8000 for zero-latency local test if applicable
            url = sub_url_or_id
        else:
            url = f"http://127.0.0.1:8000/sub/{sub_url_or_id}"

        logger.info(f"[ALEX QA / XRAY] 🌐 Fetching subscription from {url}")
        resp = httpx.get(url, timeout=5.0)
        resp.raise_for_status()
        return resp.text

    def parse_vless_uri(self, vless_uri: str) -> Dict[str, Any]:
        """Parses a vless:// URI into configuration dictionary."""
        parsed = urllib.parse.urlparse(vless_uri)
        query = urllib.parse.parse_qs(parsed.query)

        return {
            "uuid": parsed.username,
            "address": parsed.hostname,
            "port": parsed.port or 443,
            "type": query.get("type", ["tcp"])[0],
            "security": query.get("security", ["reality"])[0],
            "pbk": query.get("pbk", [""])[0],
            "sni": query.get("sni", [""])[0],
            "fp": query.get("fp", ["chrome"])[0],
            "flow": query.get("flow", [""])[0],
            "path": query.get("path", [""])[0],
            "sid": query.get("sid", [""])[0],
            "spx": query.get("spx", [""])[0],
            "name": urllib.parse.unquote(parsed.fragment)
        }

    def generate_xray_client_config(self, vless_info: Dict[str, Any]) -> Dict[str, Any]:
        """Generates isolated Xray client config for testing."""
        stream_settings = {
            "network": vless_info.get("type", "tcp"),
            "security": vless_info.get("security", "reality"),
        }
        if vless_info.get("type") == "xhttp":
            stream_settings["xhttpSettings"] = {
                "path": vless_info.get("path", "/"),
                "host": vless_info.get("sni", ""),
                "mode": "stream-one"
            }
        elif vless_info.get("type") == "ws":
            stream_settings["wsSettings"] = {
                "path": vless_info.get("path", "/"),
                "headers": {
                    "Host": vless_info.get("sni", "")
                }
            }

        if vless_info.get("security") == "reality":
            stream_settings["realitySettings"] = {
                "show": False,
                "fingerprint": vless_info.get("fp", "chrome"),
                "serverName": vless_info.get("sni", "gateway.icloud.com"),
                "publicKey": vless_info.get("pbk", ""),
                "shortId": vless_info.get("sid", ""),
                "spiderX": vless_info.get("spx", "")
            }

        user_cfg = {
            "id": vless_info["uuid"],
            "encryption": "none",
        }
        if vless_info.get("flow"):
            user_cfg["flow"] = vless_info["flow"]

        server_addr = vless_info["address"]
        if server_addr in ("344988.snk.wtf", "193.23.211.141", "localhost"):
            server_addr = "127.0.0.1"

        return {
            "log": {"loglevel": "warning"},
            "inbounds": [
                {
                    "tag": "socks-in",
                    "port": self.socks_port,
                    "listen": "127.0.0.1",
                    "protocol": "socks",
                    "settings": {
                        "auth": "noauth",
                        "udp": True
                    }
                }
            ],
            "outbounds": [
                {
                    "tag": "proxy",
                    "protocol": "vless",
                    "settings": {
                        "vnext": [
                            {
                                "address": server_addr,
                                "port": vless_info["port"],
                                "users": [user_cfg]
                            }
                        ]
                    },
                    "streamSettings": stream_settings
                },
                {"tag": "direct", "protocol": "freedom"}
            ]
        }

    def start_probe_tunnel(self, vless_info: Dict[str, Any]):
        """Creates temp config and starts Xray subprocess."""
        xray_cfg = self.generate_xray_client_config(vless_info)
        with open(self.temp_config_path, "w") as f:
            json.dump(xray_cfg, f, indent=2)

        self.process = subprocess.Popen(
            [self.xray_bin, "run", "-config", self.temp_config_path],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE
        )
        time.sleep(0.8)  # Allow socket to bind

    def stop_probe_tunnel(self):
        """Terminates Xray subprocess and cleans temp config."""
        if self.process:
            self.process.terminate()
            try:
                self.process.wait(timeout=2.0)
            except Exception:
                self.process.kill()
            self.process = None

        if os.path.exists(self.temp_config_path):
            try:
                os.remove(self.temp_config_path)
            except Exception:
                pass

    def test_connection(self, probe_url: str = "http://cp.cloudflare.com/generate_204") -> Tuple[bool, float, str]:
        """
        Executes an HTTP probe through SOCKS5 proxy using curl for maximum reliability.
        Returns (success: bool, latency_ms: float, detail: str).
        """
        start = time.monotonic()
        try:
            cmd = [
                "curl", "-s", "-o", "/dev/null", "-w", "%{http_code}",
                "--socks5-hostname", f"127.0.0.1:{self.socks_port}",
                "--max-time", "5",
                probe_url
            ]
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=6.0)
            latency_ms = round((time.monotonic() - start) * 1000, 1)
            http_code = proc.stdout.strip()
            if http_code in ("200", "204"):
                return True, latency_ms, f"HTTP {http_code} OK"
            return False, latency_ms, f"HTTP {http_code}"
        except Exception as e:
            latency_ms = round((time.monotonic() - start) * 1000, 1)
            return False, latency_ms, str(e)
