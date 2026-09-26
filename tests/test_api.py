import base64
import io
import threading
import time

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from niimbot_api import app as app_module
from niimbot_api.app import create_app
from niimbot_api.config import Settings
from niimbot_api.niimprint import RequestCodeEnum
from niimbot_api.service import PrinterConfig, PrinterService

from .conftest import FakePrinter


def png(size=(200, 100), color="black"):
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, "PNG")
    return buf.getvalue()


def settings(**kw):
    return Settings(_env_file=None, **kw)


@pytest.fixture
def make_client(service):
    clients = []

    def make(svc=None, **kw):
        c = TestClient(create_app(settings(**kw), svc or service))
        c.__enter__()
        clients.append(c)
        return c

    yield make
    for c in clients:
        c.__exit__(None, None, None)


def upload(data=None, **fields):
    return {"files": {"file": ("a.png", data or png(), "image/png")}, "data": fields}


def test_print_waits_and_returns_job(make_client, fake_printer):
    client = make_client()
    r = client.post("/v1/print", **upload(source="order-42", copies="2"))
    assert r.status_code == 200, r.text
    job = r.json()
    assert job["status"] == "done" and job["source"] == "order-42"
    assert job["result"]["copies"] == 2
    assert client.get(f"/v1/jobs/{job['id']}").json()["status"] == "done"
    assert client.get("/v1/jobs").json()[0]["id"] == job["id"]


def test_print_without_wait_returns_202(make_client, service):
    gate = threading.Event()
    real = service.print_bitmap
    service.print_bitmap = lambda *a: (gate.wait(5), real(*a))[1]
    client = make_client()
    r = client.post("/v1/print", **upload(wait="false"))
    assert r.status_code == 202 and r.json()["status"] in ("queued", "printing")
    gate.set()
    job = client.get(f"/v1/jobs/{r.json()['id']}?wait=5").json()
    assert job["status"] == "done"


def test_concurrent_requests_print_one_at_a_time(make_client, service):
    active, peak = [0], [0]
    real = service.print_bitmap

    def tracked(*a):
        active[0] += 1
        peak[0] = max(peak[0], active[0])
        time.sleep(0.05)
        try:
            return real(*a)
        finally:
            active[0] -= 1

    service.print_bitmap = tracked
    client = make_client()
    results = []
    threads = [
        threading.Thread(target=lambda: results.append(client.post("/v1/print", **upload())))
        for _ in range(5)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert [r.status_code for r in results] == [200] * 5
    assert peak[0] == 1


def test_base64_url_and_preview(make_client, fake_printer, monkeypatch):
    client = make_client()
    data = png()
    r = client.post(
        "/v1/print/base64",
        json={"image_base64": "data:image/png;base64," + base64.b64encode(data).decode()},
    )
    assert r.status_code == 200, r.text

    monkeypatch.setattr(app_module, "fetch_url", lambda url, max_bytes: data)
    r = client.post("/v1/print/url", json={"image_url": "https://example.com/l.png"})
    assert r.status_code == 200, r.text

    sent = len(fake_printer.requests)
    r = client.post("/v1/preview", **upload(label_width_mm="30", label_height_mm="15"))
    assert r.headers["content-type"] == "image/png"
    assert Image.open(io.BytesIO(r.content)).size == (240, 120)
    assert len(fake_printer.requests) == sent  # preview never touches the printer


def test_image_url_can_be_disabled(make_client):
    client = make_client(allow_image_url=False)
    r = client.post("/v1/print/url", json={"image_url": "https://example.com/l.png"})
    assert r.status_code == 403


def test_server_label_defaults_apply(make_client):
    client = make_client(label_width_mm=40, label_height_mm=30)
    r = client.post("/v1/preview", **upload())
    assert Image.open(io.BytesIO(r.content)).size == (320, 240)
    assert client.get("/v1/printer").json()["label_width_mm"] == 40


def test_bad_input_is_422(make_client):
    client = make_client()
    assert client.post("/v1/print", files={"file": ("a.txt", b"x", "text/plain")}).status_code == 422
    assert client.post("/v1/print", **upload(rotate="45")).status_code == 422
    assert client.post("/v1/print", **upload(fit="none", data=png((500, 10)))).status_code == 422


def test_printer_failure_is_503_with_job(make_client, service):
    def boom():
        raise RuntimeError("BLE scan could not find 'B1-X'")

    service._open_transport = boom
    r = make_client().post("/v1/print", **upload())
    assert r.status_code == 503
    assert r.json()["status"] == "failed" and "B1-X" in r.json()["error"]


def test_api_key_header_bearer_and_query(make_client):
    client = make_client(api_key="secret")
    assert client.get("/v1/printer").status_code == 401
    assert client.get("/v1/printer", headers={"X-API-Key": "secret"}).status_code == 200
    assert client.get("/v1/printer", headers={"Authorization": "Bearer secret"}).status_code == 200
    assert client.get("/v1/printer?api_key=secret").status_code == 200
    assert client.get("/health").status_code == 200  # open for monitoring
    assert client.get("/").status_code == 200


def test_browser_origins(make_client):
    client = make_client(cors_origins="https://myapp.com")
    # a random website cannot make your browser print, even with a "simple" form POST
    r = client.post("/v1/print", headers={"Origin": "https://evil.example"}, **upload())
    assert r.status_code == 403
    r = client.get("/v1/printer", headers={"Origin": "https://myapp.com"})
    assert r.headers["access-control-allow-origin"] == "https://myapp.com"
    r = client.get("/v1/printer", headers={"Origin": "http://localhost:5173"})
    assert r.status_code == 200
    # Chrome Private Network Access preflight (public https site -> localhost)
    r = client.options(
        "/v1/print",
        headers={
            "Origin": "https://myapp.com",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "x-api-key",
            "Access-Control-Request-Private-Network": "true",
        },
    )
    assert r.status_code == 204
    assert r.headers["access-control-allow-private-network"] == "true"
    assert r.headers["access-control-allow-headers"] == "x-api-key"


def test_wildcard_origin(make_client):
    client = make_client(cors_origins="*")
    r = client.get("/v1/printer", headers={"Origin": "https://anything.dev"})
    assert r.status_code == 200


def test_static_client_and_demo(make_client):
    client = make_client()
    r = client.get("/niimbot-client.js")
    assert "export class NiimbotClient" in r.text
    assert r.headers["content-type"].startswith("text/javascript")
    assert "Niimbot Print" in client.get("/").text


def test_keepalive_reuses_and_recovers_connection():
    opened = []

    def open_transport():
        opened.append(FakePrinter())
        return opened[-1]

    svc = PrinterService(PrinterConfig(model="b21"), keepalive_seconds=60)
    svc._open_transport = open_transport
    img = Image.new("L", (8, 8))
    svc.print_bitmap(img)
    svc.print_bitmap(img)
    assert len(opened) == 1 and svc.connected

    # connection drops while idle -> heartbeat fails -> transparent reconnect
    opened[0].write = lambda data: len(data)
    svc._last_used -= 10
    svc.print_bitmap(img)
    assert len(opened) == 2 and opened[0].closed
    svc.close()
    assert opened[1].closed and not svc.connected


def test_heartbeat_used_for_liveness(fake_printer):
    svc = PrinterService(PrinterConfig(model="b21"), keepalive_seconds=60)
    svc._open_transport = lambda: fake_printer
    svc.print_bitmap(Image.new("L", (8, 8)))
    svc._last_used -= 10
    svc.print_bitmap(Image.new("L", (8, 8)))
    assert RequestCodeEnum.HEARTBEAT in [t for t, _ in fake_printer.requests]
    svc.close()
