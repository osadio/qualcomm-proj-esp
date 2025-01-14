import sys
import json
import threading
import socket

from h2.connection import H2Connection
from h2.events import RequestReceived, DataReceived
from hyper import HTTP20Connection

# Test Mode:    Listen: localhost:9009
# Otherwise:    Listen: 10.0.3.3:8080
TEST_MODE = True if len(sys.argv) == 2 and sys.argv[1] == '--test' else False
LISTEN_PORT = 9009 if TEST_MODE else 8080

# SBA Entity Running Mode: FAST, REGULAR, WAITABLE

LOCK = threading.Lock()


def log(text):
    with open("logs/UDM.txt", "a") as f:
        f.write(str(text) + "\n")


class SBAUDMConnection(object):
    "An object of simple HTTP/2 connection"

    def __init__(self, sock, TEST_MODE, lock):
        self.sock = sock
        self.TEST_MODE = TEST_MODE
        self.lock = lock
        self.conn = H2Connection(client_side=False)

        # Space for incoming request headers
        self.rx_headers = False
        self.rx_body = False
        self.stream_id = False

        self.rx_config = 'REGULAR'  # Entity comsuming mode from header
        self.pkt_rlt = False  # Entity comsuming result
        self.fw_body = {}  # Body for fowarding

    def run_forever(self):
        self.conn.initiate_connection()
        self.sock.sendall(self.conn.data_to_send())

        while True:
            data = self.sock.recv(65535)
            if not data:
                break

            events = self.conn.receive_data(data)

            for event in events:
                if isinstance(event, RequestReceived):
                    self.rx_headers = event.headers
                    self.stream_id = event.stream_id
                elif isinstance(event, DataReceived):
                    self.rx_body = event.data

            # Foward request, wait for its response, then send response to where this request from
            if self.rx_headers and self.rx_body:
                self.send_response()
                self.resource_providing()
                if self.fw_body != {}:
                    self.foward_request()
                else:
                    log('Fowarding Failed.')

            data_to_send = self.conn.data_to_send()
            if data_to_send:
                self.sock.sendall(data_to_send)

    def resource_providing(self):
        # Parsing Request Body
        body_json = json.loads(self.rx_body)
        log(body_json)
        self.rx_config = str(body_json['MODE']['UDM'])
        self.fw_body = {
            'RESULT': body_json['RESULT']
        }
        ents = [str(x) for x in body_json['SBA_ENTITY']]
        ents.pop(0)  # delete UDM in entities list
        confs = {str(k): str(v) for k, v in body_json['MODE'].items()}
        confs.pop('UDM', None)  # delete UDM in config table

        # Fill into fowarding body
        self.fw_body.setdefault('SBA_ENTITY', [])
        self.fw_body.setdefault('MODE', {})
        self.fw_body.setdefault('RESULT', {})
        self.fw_body['SBA_ENTITY'] = ents
        self.fw_body['MODE'] = confs
        self.fw_body['RESULT']['UDM'] = True  # Always gives True for testing
        self.lock.acquire()
        log('Recieved Packet.\nMode: {}'.format(self.rx_config))
        log('RX Header: {}'.format(self.rx_headers))
        log('RX Body: {}'.format(body_json))
        log('==========RX')
        self.lock.release()

    def send_response(self):
        body = json.dumps(
            {'Content': 'Recieved by SBAES - UDM'}).encode('utf-8')
        self.conn.send_headers(
            stream_id=self.stream_id,
            headers=[
                (':status', '200'),
                ('server', 's_UDM-h2-server/1.0'),
                ('content-length', str(len(body))),
                ('content-type', 'application/json'),
                ('id', str(dict(self.rx_headers)['id']))
            ],
        )
        self.conn.send_data(
            stream_id=self.stream_id,
            data=body,
            end_stream=True
        )

    def foward_request(self):
        fw_table = {
            'NRF': 'localhost:9007',
            'AUSF': 'localhost:9008',
            'UDM': 'localhost:9009',
            'AMF': 'localhost:9010',
            'SMF': 'localhost:9011',
            'PCF': 'localhost:9012',
            'AF': 'localhost:9013'
        } if self.TEST_MODE else {
            'NRF': '10.0.3.1:8080',
            'AUSF': '10.0.3.2:8080',
            'UDM': '10.0.3.3:8080',
            'AMF': '10.0.3.4:8080',
            'SMF': '10.0.3.5:8080',
            'PCF': '10.0.3.6:8080',
            'AF': '10.0.3.7:8080'
        }
        next_ent = self.fw_body['SBA_ENTITY'][0]
        log('Fowarded packet to SBA Entity {} at http://{}'.format(next_ent,
                                                                     fw_table[next_ent]))  # Next SBA Entity to foward to
        send_conn = HTTP20Connection(fw_table[next_ent])
        fw_body = json.dumps(self.fw_body).encode('utf-8')
        send_conn.request(
            'POST', '/', headers=dict(self.rx_headers), body=fw_body)
        resp = send_conn.get_response()
        if resp:
            self.lock.acquire()
            log('Fowarded packet to SBA Entity {} at http://{}'.format(next_ent, fw_table[next_ent]))
            self.res_body = resp.read()
            log('FW Header: {}'.format(dict(self.rx_headers)))
            log('FW Body: {}'.format(fw_body))
            log('Response: {}'.format(self.res_body))
            log('==========FW')
            self.lock.release()


log('SBA Entity AMF server started at http://{}:{}'.format(
    '0.0.0.0' if TEST_MODE else '10.0.3.3', LISTEN_PORT))

sock = socket.socket()
sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
sock.bind(('0.0.0.0', int(LISTEN_PORT)))
sock.listen(5)

while True:
    try:
        connection = SBAUDMConnection(sock.accept()[0], TEST_MODE, LOCK)
        th = threading.Thread(target=connection.run_forever)
        th.start()
    except(SystemExit, KeyboardInterrupt):
        break
