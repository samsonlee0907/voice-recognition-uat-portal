import asyncio
import json

class FakeProvider:
    def __init__(self):
        self.sent = []
        self.incoming = asyncio.Queue()
        self.closed = False

    async def send(self, raw):
        message = json.loads(raw)
        self.sent.append(message)
        if message["type"] == "session.update":
            session = getattr(self, "live_session", message["session"]) if not message["session"] else message["session"]
            self.incoming.put_nowait({"type": "session.updated", "session": session})
        elif message["type"] == "session.start":
            self.incoming.put_nowait({"type": "session.started", "session": message["session"]})
        elif message["type"] == "session.close":
            self.incoming.put_nowait({"type": "session.closed", "usage": {"seconds": 0}})

    async def recv(self):
        return json.dumps(await self.incoming.get())

    async def close(self):
        self.closed = True
class FakeBrowser:
    def __init__(self):
        self.events = []
        self.incoming = asyncio.Queue()

    async def send_json(self, event):
        self.events.append(event)

    async def receive_text(self):
        return await self.incoming.get()


async def until(predicate, seconds=10):
    async with asyncio.timeout(seconds):
        while not predicate():
            await asyncio.sleep(.001)
class ResponseBody:
    def __init__(self, document):
        self.body = json.dumps(document).encode()

    async def read(self, limit):
        return self.body[:limit]

    async def iter_chunked(self, size):
        for index in range(0, len(self.body), size):
            yield self.body[index:index + size]


class Responses:
    def __init__(self, output, status="completed", http_status=200):
        self.content = ResponseBody({"status": status, "output": output})
        self.status = http_status
        self.requests = []

    def post(self, url, **kwargs):
        self.requests.append((url, kwargs))
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass
SDP = "v=0\r\no=- 1 1 IN IP4 127.0.0.1\r\ns=-\r\nt=0 0\r\nm=audio 9 UDP/TLS/RTP/SAVPF 111\r\n"


class RtcHttp:
    def __init__(self, live):
        self.live = live
        self.requests = []
        self.echo_override = {}

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass

    def post(self, url, **kwargs):
        self.requests.append((url, kwargs))
        if url.endswith("/live/sessions"):
            configuration = kwargs["json"]["session"]
            self.live.live_session = {"id": "sess_test", **configuration, **self.echo_override}
            response = Responses([], http_status=201)
            response.content = ResponseBody({"session": {"id": "sess_test"}, "transport": {"type": "webrtc", "sdp": SDP}})
            return response
        return Responses([{
            "type": "function_call", "name": "airline_service_action",
            "arguments": json.dumps({"intent": "baggage", "field": "none", "verbatim_identifier": ""}),
        }])
