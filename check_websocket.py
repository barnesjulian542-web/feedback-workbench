import time
from websockets.sync.client import connect
from streamlit.proto.BackMsg_pb2 import BackMsg
from streamlit.proto.ForwardMsg_pb2 import ForwardMsg

url='ws://127.0.0.1:8502/_stcore/stream'
origin='http://127.0.0.1:8502'

def session_id(ws):
    msg=BackMsg()
    msg.rerun_script.query_string=''
    msg.rerun_script.widget_states.SetInParent()
    ws.send(msg.SerializeToString())
    for _ in range(200):
        out=ForwardMsg()
        out.ParseFromString(ws.recv(timeout=10))
        if out.HasField('new_session'):
            value=out.new_session.initialize.session_id
            if value:return value
    raise AssertionError('Server did not initialize a session')

with connect(url,origin=origin,subprotocols=['streamlit'],proxy=None,max_queue=None,ping_interval=None) as ws:
    first=session_id(ws)
    print('WebSocket connected and page initialized',flush=True)
    for i in range(3):
        ws.ping().wait(timeout=5)
        time.sleep(12)
    print('WebSocket stayed open for 36 seconds across heartbeat interval',flush=True)
time.sleep(1)
with connect(url,origin=origin,subprotocols=['streamlit','placeholder',first],proxy=None,max_queue=None,ping_interval=None) as ws:
    second=session_id(ws)
    assert second==first,'Session was not restored after reconnect'
    ws.ping().wait(timeout=5)
print('Disconnected session successfully restored',flush=True)
