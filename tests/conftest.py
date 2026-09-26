import pytest

from niimbot_api.niimprint import BaseTransport, NiimbotPacket, RequestCodeEnum
from niimbot_api.service import PrinterConfig, PrinterService

RESPONSE_OFFSET = {
    RequestCodeEnum.SET_LABEL_TYPE: 16,
    RequestCodeEnum.SET_LABEL_DENSITY: 16,
    RequestCodeEnum.ALLOW_PRINT_CLEAR: 16,
}


class FakePrinter(BaseTransport):
    """Answers every request with success and records what was sent."""

    def __init__(self, chunk_size=None):
        self.requests = []  # (type, data)
        self.rows = {}  # y -> bitmap bytes
        self.closed = False
        self._out = bytearray()
        self._chunk = chunk_size  # simulate data arriving in pieces

    def write(self, data):
        pkt = NiimbotPacket.from_bytes(data)
        self.requests.append((pkt.type, bytes(pkt.data)))
        if pkt.type == 0x85:
            y = int.from_bytes(pkt.data[:2], "big")
            self.rows[y] = bytes(pkt.data[6:])
            return len(data)
        if pkt.type == RequestCodeEnum.GET_INFO:
            key = pkt.data[0]
            resp = NiimbotPacket(key + key, b"\x00\x64")
        elif pkt.type == RequestCodeEnum.GET_PRINT_STATUS:
            # page=1, progress 100/100, plus trailing bytes like the B1 sends
            resp = NiimbotPacket(0xB3, b"\x00\x01\x64\x64\x03\x1e\x00\x01\x00\x00")
        else:
            offset = RESPONSE_OFFSET.get(pkt.type, 1)
            resp = NiimbotPacket(pkt.type + offset, b"\x01")
        self._out.extend(resp.to_bytes())
        return len(data)

    def read(self, length):
        n = min(length, self._chunk or length)
        data = bytes(self._out[:n])
        del self._out[:n]
        return data

    def close(self):
        self.closed = True


@pytest.fixture
def fake_printer():
    return FakePrinter()


@pytest.fixture
def service(fake_printer):
    svc = PrinterService(PrinterConfig(model="b21", conn="usb"))
    svc._open_transport = lambda: fake_printer
    return svc
