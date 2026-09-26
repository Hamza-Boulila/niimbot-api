"""Bluetooth Low Energy transport for Niimbot printers (works on macOS/Windows/Linux).

niimprint's own BluetoothTransport uses RFCOMM sockets, which Python does not
support on macOS. Niimbot printers also expose a BLE serial-like service, so this
transport wraps `bleak` (async) behind niimprint's synchronous read/write API by
running an event loop in a background thread.
"""

import asyncio
import logging
import re
import threading

from bleak import BleakClient, BleakScanner
from bleak.backends.device import BLEDevice

from .niimprint import BaseTransport

SERVICE_UUID = "e7810a71-73ae-499d-8c15-faa9aef0c3f2"
CHARACTERISTIC_UUID = "bef8d6c9-9c21-4c9e-b632-bd58c1009f9f"

# Advertised name prefixes of known Niimbot models, e.g. "B21-C2091234"
NAME_PREFIXES = ("B1", "B18", "B21", "B203", "B3S", "D11", "D101", "D110")

_MAC_RE = re.compile(r"([0-9A-F]{2}:){5}[0-9A-F]{2}", re.IGNORECASE)
_UUID_RE = re.compile(r"[0-9A-F]{8}(-[0-9A-F]{4}){3}-[0-9A-F]{12}", re.IGNORECASE)


def is_niimbot(device: BLEDevice, adv=None) -> bool:
    if adv is not None and SERVICE_UUID in (adv.service_uuids or []):
        return True
    name = device.name or (adv.local_name if adv is not None else None) or ""
    return name.upper().startswith(NAME_PREFIXES)


async def scan(timeout: float = 5.0) -> list[dict]:
    """Return nearby devices that look like Niimbot printers."""
    found = await BleakScanner.discover(timeout=timeout, return_adv=True)
    result = []
    for device, adv in found.values():
        if is_niimbot(device, adv):
            result.append(
                {
                    "name": device.name or adv.local_name,
                    "address": device.address,
                    "rssi": adv.rssi,
                }
            )
    return sorted(result, key=lambda d: d["rssi"] or -999, reverse=True)


async def _find_device(addr: str | None, timeout: float) -> BLEDevice:
    """Resolve `addr` (MAC/UUID, name, name prefix, or None = first printer)."""
    if addr and (_MAC_RE.fullmatch(addr) or _UUID_RE.fullmatch(addr)):
        device = await BleakScanner.find_device_by_address(addr, timeout=timeout)
    else:
        needle = (addr or "").upper()

        def match(d, adv):
            name = (d.name or adv.local_name or "").upper()
            if needle:
                return name.startswith(needle)
            return is_niimbot(d, adv)

        device = await BleakScanner.find_device_by_filter(match, timeout=timeout)
    if device is None:
        what = f"'{addr}'" if addr else "any Niimbot printer"
        raise RuntimeError(f"BLE scan could not find {what}; is the printer on?")
    return device


class BLETransport(BaseTransport):
    def __init__(self, address: str | None = None, timeout: float = 10.0):
        self._buf = bytearray()
        self._cond = threading.Condition()
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._loop.run_forever, daemon=True)
        self._thread.start()
        self._client: BleakClient | None = None
        try:
            self._run(self._connect(address, timeout), timeout + 15)
        except BaseException:
            self.close()
            raise

    def _run(self, coro, timeout: float | None = None):
        return asyncio.run_coroutine_threadsafe(coro, self._loop).result(timeout)

    async def _connect(self, address, timeout):
        device = await _find_device(address, timeout)
        logging.info(f"Connecting to {device.name} [{device.address}]")
        self._client = BleakClient(device)
        await self._client.connect()
        self._char = self._pick_characteristic()
        await self._client.start_notify(self._char, self._on_notify)

    def _pick_characteristic(self):
        char = self._client.services.get_characteristic(CHARACTERISTIC_UUID)
        if char is not None:
            return char
        # Fall back to any notify + write characteristic in the printer service
        service = self._client.services.get_service(SERVICE_UUID)
        for c in service.characteristics if service else []:
            if "notify" in c.properties and "write-without-response" in c.properties:
                return c
        raise RuntimeError("Printer does not expose the expected BLE characteristic")

    def _on_notify(self, _sender, data: bytearray):
        with self._cond:
            self._buf.extend(data)
            self._cond.notify_all()

    def read(self, length: int) -> bytes:
        with self._cond:
            self._cond.wait_for(lambda: len(self._buf) > 0, timeout=0.5)
            data = bytes(self._buf[:length])
            del self._buf[:length]
            return data

    def write(self, data: bytes):
        chunk = self._char.max_write_without_response_size
        for i in range(0, len(data), chunk):
            self._run(
                self._client.write_gatt_char(
                    self._char, data[i : i + chunk], response=False
                ),
                10,
            )
        return len(data)

    @property
    def is_connected(self) -> bool:
        return self._client is not None and self._client.is_connected

    def close(self):
        try:
            if self._client is not None and self._client.is_connected:
                self._run(self._client.disconnect(), 10)
        except Exception:
            logging.debug("Error while disconnecting", exc_info=True)
        finally:
            self._loop.call_soon_threadsafe(self._loop.stop)
            self._thread.join(timeout=5)
