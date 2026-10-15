import socket
import json

class UDPSender:
    def __init__(self, port=9090):
        self.addr = ('127.0.0.1', port)
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.setblocking(False)
    
    def send(self, data):
        try:
            self.sock.sendto(json.dumps(data).encode('utf-8'), self.addr)
        except:
            pass
