"""Printer connection handling shared by the HTTP API and the CLI."""

import logging
import os
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Literal

from PIL import Image

from .imaging import MODELS, ImageOptions, PrinterModel, prepare_image
from .niimprint import BluetoothTransport, InfoEnum, PrinterClient, SerialTransport

Connection = Literal["usb", "ble", "bluetooth"]


@dataclass
class PrinterConfig:
    model: str = "b21"
    conn: Connection = "usb"
    # usb: serial port path (None = auto-detect)
    # ble: MAC/UUID, name or name prefix like "B21-" (None = first printer found)
    # bluetooth: MAC address (Linux RFCOMM only)
    addr: str | None = None
    density: int = 3

    @classmethod
    def from_env(cls) -> "PrinterConfig":
        return cls(
            model=os.getenv("NIIMBOT_MODEL", "b21").lower(),
            conn=os.getenv("NIIMBOT_CONN", "usb").lower(),
            addr=os.getenv("NIIMBOT_ADDR") or None,
            density=int(os.getenv("NIIMBOT_DENSITY", "3")),
        )

    @property
    def spec(self) -> PrinterModel:
        try:
            return MODELS[self.model]
        except KeyError:
            raise ValueError(
                f"Unknown model '{self.model}', expected one of {', '.join(MODELS)}"
            ) from None


@dataclass
class PrintJob:
    image: Image.Image
    options: ImageOptions = field(default_factory=ImageOptions)
    density: int | None = None
    copies: int = 1


class PrinterService:
    """Serialises access to the printer and optionally keeps the connection open.

    With keepalive_seconds > 0 the connection is reused between jobs and closed
    after that many idle seconds; a dropped connection is re-opened transparently.
    """

    # Ping a reused connection before a job if it has been idle this long
    PING_AFTER_IDLE = 5.0

    def __init__(self, config: PrinterConfig, keepalive_seconds: float = 0):
        self.config = config
        config.spec  # validate model early
        self.keepalive_seconds = keepalive_seconds
        self._lock = threading.RLock()
        self._transport = None
        self._client: PrinterClient | None = None
        self._last_used = 0.0
        self._closed = threading.Event()
        if keepalive_seconds > 0:
            threading.Thread(target=self._reaper, daemon=True).start()

    @property
    def connected(self) -> bool:
        return self._client is not None

    def _open_transport(self):
        c = self.config
        if c.conn == "usb":
            return SerialTransport(port=c.addr or "auto")
        if c.conn == "ble":
            from .ble import BLETransport

            return BLETransport(c.addr)
        if c.conn == "bluetooth":
            if not c.addr:
                raise ValueError("A MAC address is required for bluetooth connection")
            return BluetoothTransport(c.addr.upper())
        raise ValueError(f"Unknown connection type '{c.conn}'")

    @contextmanager
    def client(self):
        with self._lock:
            client = self._get_client()
            try:
                yield client
            except BaseException:
                self._disconnect()  # state unknown after an error, start fresh
                raise
            finally:
                self._last_used = time.monotonic()
                if self.keepalive_seconds <= 0:
                    self._disconnect()

    def _get_client(self) -> PrinterClient:
        if self._client is not None:
            alive = getattr(self._transport, "is_connected", True)
            if alive and time.monotonic() - self._last_used > self.PING_AFTER_IDLE:
                try:
                    self._client.heartbeat()
                except Exception as e:
                    logging.info(f"Printer connection went stale ({e!r}), reconnecting")
                    alive = False
            if alive:
                return self._client
            self._disconnect()
        self._transport = self._open_transport()
        self._client = PrinterClient(self._transport)
        logging.info("Printer connected")
        return self._client

    def _disconnect(self):
        if self._transport is not None:
            try:
                self._transport.close()
            except Exception:
                logging.debug("Error closing transport", exc_info=True)
            logging.info("Printer disconnected")
        self._transport = None
        self._client = None

    def _reaper(self):
        while not self._closed.wait(2):
            if self._client is None or not self._lock.acquire(blocking=False):
                continue
            try:
                idle = time.monotonic() - self._last_used
                if self._client is not None and idle > self.keepalive_seconds:
                    self._disconnect()
            finally:
                self._lock.release()

    def close(self):
        self._closed.set()
        with self._lock:
            self._disconnect()

    def render(self, job: PrintJob) -> Image.Image:
        return prepare_image(job.image, self.config.spec, job.options)

    def print(self, job: PrintJob) -> dict:
        return self.print_bitmap(self.render(job), job.density, job.copies)

    def print_bitmap(
        self, bitmap: Image.Image, density: int | None = None, copies: int = 1
    ) -> dict:
        """Print an image already produced by render()."""
        if not 1 <= copies <= 100:
            raise ValueError("copies must be between 1 and 100")
        density = self._clamp_density(density or self.config.density)
        with self.client() as printer:
            for i in range(copies):
                logging.info(f"Printing copy {i + 1}/{copies}")
                if self.config.spec.protocol == "v2":
                    # Pace rows over BLE so the printer's buffer keeps up
                    row_delay = 0.01 if self.config.conn == "ble" else 0
                    printer.print_image_v2(bitmap, density=density, row_delay=row_delay)
                else:
                    printer.print_image(bitmap, density=density)
        return {
            "width_px": bitmap.width,
            "height_px": bitmap.height,
            "density": density,
            "copies": copies,
        }

    def status(self) -> dict:
        with self.client() as printer:
            info = {}
            for key in (
                InfoEnum.DEVICETYPE,
                InfoEnum.DEVICESERIAL,
                InfoEnum.SOFTVERSION,
                InfoEnum.HARDVERSION,
                InfoEnum.BATTERY,
            ):
                try:
                    info[key.name.lower()] = printer.get_info(key)
                except Exception as e:  # not every model answers every key
                    logging.debug(f"get_info({key.name}) failed: {e!r}")
                    info[key.name.lower()] = None
            for name, fn in (("heartbeat", printer.heartbeat), ("label", printer.get_rfid)):
                try:
                    info[name] = fn()
                except Exception as e:
                    logging.debug(f"{name} failed: {e!r}")
                    info[name] = None
            return info

    def _clamp_density(self, density: int) -> int:
        spec = self.config.spec
        if not 1 <= density <= 5:
            raise ValueError("density must be between 1 and 5")
        if density > spec.max_density:
            logging.warning(
                f"{spec.name.upper()} supports density up to {spec.max_density}"
            )
            density = spec.max_density
        return density
