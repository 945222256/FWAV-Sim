import socket
import json
import logging
from typing import Optional, Dict, Any

logger = logging.getLogger("usv_network")

class UDPServer:
    """Non-blocking UDP server to receive JSON payloads."""
    def __init__(self, port: int, bind_ip: str = "0.0.0.0", timeout: float = 0.0):
        # bind_ip 默认 0.0.0.0：需接收 WSL/外部主机指令（原 127.0.0.1 只收本机回环）
        self.port = port
        self.bind_ip = bind_ip
        self.last_addr = None  # 最近数据包来源 (ip, port)
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        # Prevent "address already in use" errors during frequent restarts
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind((self.bind_ip, self.port))
        self.sock.settimeout(timeout)  # Non-blocking by default

    def receive(self) -> Optional[Dict[str, Any]]:
        """Receive a JSON payload non-blockingly."""
        try:
            data, addr = self.sock.recvfrom(4096)
            if data:
                self.last_addr = addr
                return json.loads(data.decode("utf-8"))
        except BlockingIOError:
            pass # No data available
        except socket.timeout:
            pass # No data available
        except json.JSONDecodeError:
            logger.warning("Received invalid JSON data over UDP")
        except Exception as e:
            logger.error(f"UDP receive error: {e}")
        return None

    def get_last_addr(self):
        """最近数据包来源地址 (ip, port)——遥测回发目标据此学习（NAT 下 WSL IP 会漂移）。"""
        return self.last_addr

    def close(self):
        self.sock.close()


class UDPClient:
    """UDP client to send JSON payloads."""
    def __init__(self, target_port: int, target_ip: str = "127.0.0.1"):
        self.target_port = target_port
        self.target_ip = target_ip
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

    def set_target(self, ip: str):
        """动态切换目标地址（配 UDPServer.get_last_addr 做对端学习）。"""
        self.target_ip = ip

    def send(self, data: Dict[str, Any]):
        """Send a dict as a JSON payload."""
        try:
            payload = json.dumps(data).encode("utf-8")
            self.sock.sendto(payload, (self.target_ip, self.target_port))
        except Exception as e:
            logger.error(f"UDP send error: {e}")

    def close(self):
        self.sock.close()
