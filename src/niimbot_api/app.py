"""HTTP API for printing images on Niimbot label printers."""

import base64
import binascii
import io
import logging
import re
from contextlib import asynccontextmanager
from importlib.resources import files
from typing import Annotated

import httpx
from fastapi import APIRouter, Depends, FastAPI, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.security import APIKeyHeader
from PIL import Image, UnidentifiedImageError
from pydantic import BaseModel, Field, field_validator

from .config import Settings
from .imaging import FitMode, ImageOptions
from .jobs import PrintQueue
from .service import PrinterConfig, PrinterService, PrintJob

VERSION = "0.2.0"
API_KEY_HEADER = APIKeyHeader(name="X-API-Key", auto_error=False)
LOCALHOST_ORIGIN = re.compile(r"^https?://(localhost|127\.0\.0\.1|\[::1\])(:\d+)?$")


class ImageSettings(BaseModel):
    rotate: int = Field(0, description="Clockwise rotation: 0, 90, 180 or 270")
    label_width_mm: float | None = Field(
        None, gt=0, description="Label size across the print head (server default if unset)"
    )
    label_height_mm: float | None = Field(
        None, gt=0, description="Label length along the feed (server default if unset)"
    )
    fit: FitMode = Field("contain", description="contain | cover | stretch | none")
    dither: bool | None = Field(
        None, description="Dither photos; false = sharp threshold for text/barcodes"
    )
    threshold: int = Field(128, ge=0, le=255)
    invert: bool = False

    @field_validator("rotate")
    @classmethod
    def _rotate(cls, v):
        if v not in (0, 90, 180, 270):
            raise ValueError("rotate must be 0, 90, 180 or 270")
        return v

    def image_options(self, s: Settings) -> ImageOptions:
        explicit_size = self.label_width_mm or self.label_height_mm
        return ImageOptions(
            rotate=self.rotate,
            label_width_mm=self.label_width_mm if explicit_size else s.label_width_mm,
            label_height_mm=self.label_height_mm if explicit_size else s.label_height_mm,
            fit=self.fit,
            dither=s.dither if self.dither is None else self.dither,
            threshold=self.threshold,
            invert=self.invert,
        )


class PrintSettings(ImageSettings):
    density: int | None = Field(None, ge=1, le=5, description="Server default if unset")
    copies: int = Field(1, ge=1, le=100)
    source: str | None = Field(
        None, max_length=200, description="Your own reference (order id...), echoed back"
    )
    wait: bool = Field(
        True, description="Wait for the print to finish; false returns 202 immediately"
    )
    wait_timeout: float = Field(60, gt=0, le=300, description="Seconds to wait")


class PrintForm(PrintSettings):
    file: UploadFile = Field(..., description="Image to print (PNG, JPEG, ...)")


class PreviewForm(ImageSettings):
    file: UploadFile = Field(..., description="Image to preview")


class Base64PrintRequest(PrintSettings):
    image_base64: str = Field(..., description="Image bytes as base64 or a data: URL")


class UrlPrintRequest(PrintSettings):
    image_url: str = Field(..., description="http(s) URL of the image to print")


def create_app(
    settings: Settings | None = None, service: PrinterService | None = None
) -> FastAPI:
    s = settings or Settings()
    service = service or PrinterService(
        PrinterConfig(s.model, s.conn, s.addr, s.density),
        keepalive_seconds=s.keepalive_seconds,
    )
    jobs = PrintQueue(service, history=s.job_history)
    max_bytes = s.max_upload_mb * 1024 * 1024

    @asynccontextmanager
    async def lifespan(app):
        if s.host not in ("127.0.0.1", "localhost", "::1") and not s.api_key:
            logging.warning("Listening beyond localhost without NIIMBOT_API_KEY set!")
        yield
        jobs.stop()
        service.close()

    app = FastAPI(
        title="Niimbot Print API",
        description=(
            "Print images on a Niimbot label printer from any app. "
            "Authenticate with `X-API-Key` (or `Authorization: Bearer`) when the "
            "server has NIIMBOT_API_KEY set."
        ),
        version=VERSION,
        lifespan=lifespan,
    )
    app.state.jobs = jobs
    app.state.service = service
    app.state.settings = s

    # --- Browser access: CORS, Chrome Private Network Access, cross-site guard ---

    def origin_allowed(origin: str, request: Request) -> bool:
        if "*" in s.cors_origins or origin in s.cors_origins:
            return True
        if LOCALHOST_ORIGIN.match(origin):
            return True
        return origin == f"{request.url.scheme}://{request.headers.get('host')}"

    @app.middleware("http")
    async def browser_guard(request: Request, call_next):
        origin = request.headers.get("origin")
        if not origin:  # curl, backends, same-origin GETs
            return await call_next(request)
        allowed = origin_allowed(origin, request)
        is_preflight = (
            request.method == "OPTIONS"
            and "access-control-request-method" in request.headers
        )
        if not allowed:
            # Blocks other websites from printing via your browser (CORS alone
            # would still let "simple" form POSTs through).
            return JSONResponse(
                {"detail": f"Origin {origin} is not allowed; add it to NIIMBOT_CORS_ORIGINS"},
                status_code=403,
            )
        if is_preflight:
            response = Response(status_code=204)
            response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
            response.headers["Access-Control-Allow-Headers"] = request.headers.get(
                "access-control-request-headers", "*"
            )
            response.headers["Access-Control-Max-Age"] = "600"
            if request.headers.get("access-control-request-private-network") == "true":
                response.headers["Access-Control-Allow-Private-Network"] = "true"
        else:
            response = await call_next(request)
        response.headers["Access-Control-Allow-Origin"] = origin
        response.headers["Vary"] = "Origin"
        return response

    # --- Auth ---

    def check_key(
        request: Request, header_key: Annotated[str | None, Depends(API_KEY_HEADER)]
    ):
        if not s.api_key:
            return
        auth = request.headers.get("authorization", "")
        given = (
            header_key
            or (auth[7:] if auth.lower().startswith("bearer ") else None)
            or request.query_params.get("api_key")
        )
        if given != s.api_key:
            raise HTTPException(401, "Invalid or missing API key")

    # --- Helpers ---

    def render(image: Image.Image, opts: ImageSettings):
        try:
            return service.render(PrintJob(image, opts.image_options(s)))
        except ValueError as e:
            raise HTTPException(422, str(e))

    def enqueue(image: Image.Image, opts: PrintSettings):
        bitmap = render(image, opts)
        job = jobs.submit(bitmap, opts.density, opts.copies, opts.source)
        if opts.wait:
            job.wait(opts.wait_timeout)
        body = job.to_dict(jobs.jobs_ahead(job))
        if job.status == "failed":
            return JSONResponse(body, status_code=503)
        return JSONResponse(body, status_code=200 if job.status == "done" else 202)

    def load_upload(file: UploadFile) -> Image.Image:
        data = file.file.read(max_bytes + 1)
        if len(data) > max_bytes:
            raise HTTPException(413, f"Image larger than {s.max_upload_mb} MB")
        return load_bytes(data)

    def printer_call(fn):
        try:
            return fn()
        except (TimeoutError, RuntimeError, OSError) as e:
            logging.exception("Printer error")
            raise HTTPException(503, f"Printer error: {e}")

    # --- Routes ---

    @app.get("/", include_in_schema=False)
    def demo_page():
        return HTMLResponse(files("niimbot_api").joinpath("static/index.html").read_text())

    @app.get("/niimbot-client.js", include_in_schema=False)
    def js_client():
        return Response(
            files("niimbot_api").joinpath("static/niimbot-client.js").read_text(),
            media_type="text/javascript",
        )

    @app.get("/health", tags=["meta"])
    def health():
        return {"ok": True, "version": VERSION}

    v1 = APIRouter(prefix="/v1", dependencies=[Depends(check_key)])

    @v1.post("/print", tags=["print"], status_code=200, responses={202: {}, 503: {}})
    def print_upload(form: Annotated[PrintForm, Form()]):
        """Print an uploaded image (multipart/form-data)."""
        return enqueue(load_upload(form.file), form)

    @v1.post("/print/base64", tags=["print"], status_code=200, responses={202: {}, 503: {}})
    def print_base64(req: Base64PrintRequest):
        """Print a base64 image or data: URL (e.g. from canvas.toDataURL())."""
        return enqueue(load_bytes(decode_b64(req.image_base64)), req)

    @v1.post("/print/url", tags=["print"], status_code=200, responses={202: {}, 503: {}})
    def print_url(req: UrlPrintRequest):
        """Download an image from a URL and print it."""
        if not s.allow_image_url:
            raise HTTPException(403, "Printing from URLs is disabled on this server")
        return enqueue(load_bytes(fetch_url(req.image_url, max_bytes)), req)

    @v1.post(
        "/preview", tags=["print"], response_class=Response,
        responses={200: {"content": {"image/png": {}}}},
    )
    def preview(form: Annotated[PreviewForm, Form()]):
        """Return the exact 1-bit bitmap that would be printed, without printing."""
        buf = io.BytesIO()
        render(load_upload(form.file), form).save(buf, "PNG")
        return Response(buf.getvalue(), media_type="image/png")

    @v1.get("/jobs", tags=["jobs"])
    def list_jobs(limit: int = 50):
        return [j.to_dict(jobs.jobs_ahead(j)) for j in jobs.list(limit)]

    @v1.get("/jobs/{job_id}", tags=["jobs"])
    def get_job(job_id: str, wait: float = 0):
        """Job status. Pass ?wait=<seconds> to long-poll until it finishes."""
        job = jobs.get(job_id)
        if job is None:
            raise HTTPException(404, "Job not found")
        if wait > 0:
            job.wait(min(wait, 300))
        return job.to_dict(jobs.jobs_ahead(job))

    @v1.get("/printer", tags=["printer"])
    def printer_info():
        """Configured printer, label defaults and queue state (no printer I/O)."""
        c = service.config
        return {
            "model": c.model,
            "conn": c.conn,
            "addr": c.addr,
            "connected": service.connected,
            "density": c.density,
            "max_width_px": c.spec.max_width_px,
            "max_density": c.spec.max_density,
            "dots_per_mm": 8,
            "label_width_mm": s.label_width_mm,
            "label_height_mm": s.label_height_mm,
            "dither": s.dither,
            "pending_jobs": jobs.pending,
        }

    @v1.get("/printer/status", tags=["printer"])
    def printer_status():
        """Battery, firmware and loaded label read from the printer."""
        return printer_call(service.status)

    @v1.get("/printers/scan", tags=["printer"])
    async def scan_printers(timeout: float = 5.0):
        """Scan for nearby Niimbot printers over Bluetooth LE."""
        from .ble import scan

        return await scan(min(timeout, 30))

    app.include_router(v1)
    return app


def load_bytes(data: bytes) -> Image.Image:
    try:
        image = Image.open(io.BytesIO(data))
        image.load()
        return image
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError):
        raise HTTPException(422, "Could not decode image")


def decode_b64(value: str) -> bytes:
    if value.startswith("data:"):
        value = value.split(",", 1)[-1]
    try:
        return base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError):
        raise HTTPException(422, "image_base64 is not valid base64")


def fetch_url(url: str, max_bytes: int) -> bytes:
    if not url.startswith(("http://", "https://")):
        raise HTTPException(422, "image_url must be http(s)")
    try:
        with httpx.stream("GET", url, timeout=15, follow_redirects=True) as r:
            r.raise_for_status()
            data = bytearray()
            for chunk in r.iter_bytes():
                data += chunk
                if len(data) > max_bytes:
                    raise HTTPException(413, "Image too large")
            return bytes(data)
    except httpx.HTTPError as e:
        raise HTTPException(422, f"Could not download image: {e}")
