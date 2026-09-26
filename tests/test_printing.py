import io

import pytest
from PIL import Image

from niimbot_api.imaging import MODELS, ImageOptions, prepare_image
from niimbot_api.niimprint import NiimbotPacket, PrinterClient, RequestCodeEnum
from niimbot_api.service import PrintJob

from .conftest import FakePrinter


def png_bytes(img):
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


def test_packet_roundtrip():
    pkt = NiimbotPacket(0x21, b"\x03")
    assert NiimbotPacket.from_bytes(pkt.to_bytes()).data == b"\x03"


def test_recv_handles_partial_and_garbage_data():
    transport = FakePrinter(chunk_size=3)
    transport._out.extend(b"\x00\xff")  # leading garbage
    transport._out.extend(NiimbotPacket(0x02, b"\x01").to_bytes())
    client = PrinterClient(transport)
    packets = []
    for _ in range(10):
        packets += client._recv()
    assert [p.type for p in packets] == [0x02]


def test_silent_printer_raises_timeout():
    class Silent(FakePrinter):
        def write(self, data):
            return len(data)

    with pytest.raises(TimeoutError):
        PrinterClient(Silent()).start_print()


def test_prepare_contain_pads_to_label_size():
    img = Image.new("RGB", (100, 100), "black")
    out = prepare_image(
        img, MODELS["b21"], ImageOptions(label_width_mm=40, label_height_mm=20)
    )
    assert out.mode == "1" and out.size == (320, 160)
    assert out.getpixel((0, 0)) == 255  # white padding on the side
    assert out.getpixel((160, 80)) == 0  # black content in the middle


def test_prepare_clamps_to_print_head_and_rotates():
    img = Image.new("L", (1000, 200), 0)
    assert prepare_image(img, MODELS["b21"], ImageOptions()).size == (384, 77)
    # 40x12 mm D11 label designed in landscape: rotate so 12 mm is across the head
    img = Image.new("L", (320, 96), 0)
    out = prepare_image(img, MODELS["d11"], ImageOptions(rotate=90, fit="none"))
    assert out.size == (96, 320)


def test_prepare_rejects_too_wide_without_fit():
    with pytest.raises(ValueError):
        prepare_image(Image.new("L", (500, 50)), MODELS["b21"], ImageOptions(fit="none"))


def test_transparent_background_becomes_white():
    img = Image.new("RGBA", (8, 8), (0, 0, 0, 0))
    out = prepare_image(img, MODELS["b21"], ImageOptions())
    assert out.getextrema() == (255, 255)


def test_service_prints_bitmap(service, fake_printer):
    img = Image.new("L", (16, 4), 255)
    img.putpixel((0, 0), 0)
    result = service.print(PrintJob(img, density=5, copies=2))
    assert result == {"width_px": 16, "height_px": 4, "density": 5, "copies": 2}
    types = [t for t, _ in fake_printer.requests]
    assert types.count(RequestCodeEnum.START_PRINT) == 2
    assert types.count(0x85) == 8
    assert fake_printer.rows[0] == b"\x80\x00"  # black pixel -> set bit
    assert fake_printer.rows[1] == b"\x00\x00"
    assert fake_printer.closed


def test_density_clamped_for_d11(fake_printer):
    from niimbot_api.service import PrinterConfig, PrinterService

    svc = PrinterService(PrinterConfig(model="d11"))
    svc._open_transport = lambda: fake_printer
    assert svc.print(PrintJob(Image.new("L", (8, 8)), density=5))["density"] == 3


def test_b1_uses_v2_print_flow(fake_printer):
    from niimbot_api.service import PrinterConfig, PrinterService

    svc = PrinterService(PrinterConfig(model="b1"))
    svc._open_transport = lambda: fake_printer
    img = Image.new("L", (24, 2), 255)
    img.putpixel((0, 0), 0)
    svc.print(PrintJob(img, copies=2))
    starts = [d for t, d in fake_printer.requests if t == RequestCodeEnum.START_PRINT]
    assert starts == [b"\x00\x01\x00\x00\x00\x00\x00"] * 2  # one job per copy
    req = dict(fake_printer.requests)
    assert req[RequestCodeEnum.SET_DIMENSION] == b"\x00\x02\x00\x18\x00\x01"
    row0 = next(d for t, d in fake_printer.requests if t == 0x85)
    assert row0[2:5] == b"\x01\x00\x00"  # one black pixel in the first head third
    assert fake_printer.requests[-1][0] == RequestCodeEnum.END_PRINT
