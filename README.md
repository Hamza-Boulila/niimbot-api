# niimbot-api

> [!WARNING]
> **This is not an official Niimbot product.** It is an independent, community-made project and is not
> affiliated with, endorsed by, or supported by Niimbot or its parent company. "Niimbot" and the printer model
> names are trademarks of their respective owners and are used here only to describe compatibility. The printer
> protocol was reverse-engineered, so a firmware update may break it at any time. Use at your own risk; see
> [Disclaimer](#disclaimer).

A local print server for Niimbot label printers (B1, B18, B21, D11, D110). Any web app,
backend or script can print images on the printer through a small HTTP API.
Built on [niimprint](https://github.com/AndBondStyle/niimprint); see [Credits](#credits).

```
your web app ──HTTP──▶ niimbot-api (this, on the computer near the printer) ──BLE/USB──▶ printer
```

- Accepts PNG, JPEG and other images as a file upload, base64 or data URL, or a URL. Scales them to the label and converts them to 1-bit.
- A queue prints requests one at a time, so many apps can share the printer.
- Keeps the Bluetooth connection open between jobs, so later prints skip the ~3 s connect.
- Supports browsers through CORS and Chrome's Private Network Access, and blocks unknown websites from printing.
- Optional API key.
- `/docs` has interactive OpenAPI docs, and `/` has a drag-and-drop test page.

## Quick start

```sh
uv sync
cp .env.example .env        # set model / connection / address
uv run niimbot serve        # http://127.0.0.1:8000  (test page) and /docs
```

**Finding the printer:**
- Bluetooth: run `uv run niimbot scan` and put the name (e.g. `B1-XXXXXXXX`) in `NIIMBOT_ADDR`. Don't pair it in macOS Bluetooth settings, and close the Niimbot phone app.
- USB: set `NIIMBOT_CONN=usb`. The port is auto-detected.

| `NIIMBOT_CONN` | Use for | `NIIMBOT_ADDR` |
|---|---|---|
| `ble` | Bluetooth on macOS/Windows/Linux | name/prefix (`B1-`), BLE address, or empty = first found |
| `usb` | USB cable | serial port, e.g. `/dev/cu.usbmodem1101`, or empty = auto |
| `bluetooth` | Classic RFCOMM, Linux only | MAC address |

Tested: **B1 over BLE** (firmware 13.06). The B1 automatically uses its newer print protocol.

## Using it from a web app

### JavaScript client (browser or Node 18+)

The server hosts a zero-dependency client:

```js
import { NiimbotClient } from "http://127.0.0.1:8000/niimbot-client.js";
// or copy src/niimbot_api/static/niimbot-client.js into your project

const printer = new NiimbotClient("http://127.0.0.1:8000", { apiKey: "optional" });

await printer.print(fileInput.files[0], { labelWidthMm: 50, labelHeightMm: 30 });
await printer.print(canvas, { dither: false, copies: 2 });            // <canvas> you drew the label on
await printer.print("https://myapp.com/labels/42.png", { source: "order-42" });
await printer.print(canvas.toDataURL());                               // data: URL

const png = await printer.preview(file, { labelWidthMm: 50 });        // Blob, no printing
const job = await printer.print(file, { wait: false });               // returns immediately
await printer.job(job.id, 30);                                        // long-poll until done
await printer.status();                                                // battery, label roll…
```

Errors throw `NiimbotError`, which has `.status` (HTTP code) and `.body` (the job, when printing failed).

### Plain `fetch`

```js
const form = new FormData();
form.append("file", blob, "label.png");
form.append("label_width_mm", "50");
form.append("label_height_mm", "30");
const res = await fetch("http://127.0.0.1:8000/v1/print", { method: "POST", body: form });
const job = await res.json();   // { id, status: "done", ... }
```

### From a backend (any language)

```sh
curl -F file=@label.png -F label_width_mm=50 -F label_height_mm=30 http://127.0.0.1:8000/v1/print
curl -H 'Content-Type: application/json' -d '{"image_url":"https://…/label.png","copies":2}' \
     http://127.0.0.1:8000/v1/print/url
```

```python
requests.post("http://127.0.0.1:8000/v1/print", files={"file": open("label.png", "rb")},
              data={"label_width_mm": 50, "label_height_mm": 30}).raise_for_status()
```

## API reference

All endpoints are under `/v1`. The full schema is at `/docs`.

| Method | Path | |
|---|---|---|
| `POST` | `/v1/print` | multipart: `file` + options |
| `POST` | `/v1/print/base64` | JSON: `image_base64` (base64 or data URL) + options |
| `POST` | `/v1/print/url` | JSON: `image_url` + options (server downloads it) |
| `POST` | `/v1/preview` | multipart; returns the 1-bit PNG that would print |
| `GET` | `/v1/jobs` · `/v1/jobs/{id}?wait=30` | job history / one job (optional long-poll) |
| `GET` | `/v1/printer` | model, label defaults, connected, queue length (no printer I/O) |
| `GET` | `/v1/printer/status` | battery, firmware, loaded label roll (talks to printer) |
| `GET` | `/v1/printers/scan` | nearby BLE printers |
| `GET` | `/health` | liveness (no auth) |

**Print options** (all optional; server defaults come from `.env`):

| Field | Default | |
|---|---|---|
| `label_width_mm` / `label_height_mm` | `NIIMBOT_LABEL_*` | label size; width runs across the print head (8 px/mm, max 384 px B-series / 96 px D-series) |
| `fit` | `contain` | `contain` (pad), `cover` (crop), `stretch`, `none` |
| `rotate` | `0` | clockwise `0/90/180/270`, applied before fitting |
| `dither` | `NIIMBOT_DITHER` | `true` for photos; `false` for sharp text, barcodes and QR codes |
| `threshold`, `invert` | `128`, `false` | black/white cutoff when not dithering; invert colours |
| `density` | `NIIMBOT_DENSITY` | darkness 1–5 (B18/D11/D110 max 3) |
| `copies` | `1` | 1–100 |
| `source` | – | your reference (order id…), returned on the job |
| `wait` / `wait_timeout` | `true` / `60` | wait for the print to finish, or return immediately |

**Responses:**
- **200:** printed.
- **202:** queued or still printing (`wait=false` or timed out). Poll `/v1/jobs/{id}`.
- **422:** bad image or options.
- **503:** printer error. The body is the failed job, with `error` set.
- **401/403:** bad API key, or a website origin that isn't allowed.

```json
{ "id": "6945bf8c8cf5", "status": "done", "source": "order-42", "copies": 1,
  "width_px": 320, "height_px": 240, "result": {...}, "error": null, "jobs_ahead": 0 }
```

## Where your web app runs

**Same computer (e.g. `localhost:3000`).** Works out of the box, because localhost origins are always allowed.

**A deployed site (`https://myapp.com`) that prints from the user's browser.** The browser calls `http://127.0.0.1:8000` on the computer the printer is connected to.
- Add the site to `NIIMBOT_CORS_ORIGINS=https://myapp.com`. Any other website gets `403`, so a random site you visit can't print on your printer.
- Chrome may ask once for "local network access". Safari can be stricter about HTTPS pages calling `http://127.0.0.1`. If it blocks the call, use the tunnel option below.

**Other devices on your network.** Set `NIIMBOT_HOST=0.0.0.0` and `NIIMBOT_API_KEY=...`, then call `http://<mac-ip>:8000`.

**A cloud backend (server-to-server).** Expose the print server with a tunnel and set an API key:
- `cloudflared tunnel --url http://127.0.0.1:8000`
- `tailscale funnel 8000`
- For a private tailnet, use `tailscale serve`.

## Running in the background (macOS)

The install script sets the server up as a macOS LaunchAgent (`com.niimbot.api`). The server then keeps running
after you close the terminal, restarts itself if it crashes, and starts again automatically each time you log in,
including after a shutdown or reboot.

```sh
# Install and start (run from the project folder)
scripts/install-service.sh

# If `uv` is not on your PATH (e.g. installed in ~/.local/bin):
PATH="$HOME/.local/bin:$PATH" sh scripts/install-service.sh

# Check that it is running
curl http://127.0.0.1:8000/health
launchctl print gui/$(id -u)/com.niimbot.api | grep -E "state|pid"

# Watch the logs
tail -f logs/server.log

# Restart it (e.g. after editing .env)
launchctl kickstart -k gui/$(id -u)/com.niimbot.api

# Stop it and remove it from startup
PATH="$HOME/.local/bin:$PATH" sh scripts/install-service.sh uninstall
```

**Starting after a power-on.** The service starts when you log in, not at the login screen, because macOS only
allows Bluetooth for a logged-in user. To have it start with nobody at the Mac, turn on System Settings ›
Users & Groups › "Automatically log in as…". macOS won't allow automatic login while FileVault is on.

It reads `.env` from the project folder. If Bluetooth fails only when running as a service, allow Bluetooth for the
Python binary in System Settings › Privacy & Security › Bluetooth, or run `uv run niimbot serve` in a
terminal instead.

Each server drives one printer. For a second printer, run another instance with a different
`NIIMBOT_ADDR` and `NIIMBOT_PORT`.

## CLI

```sh
uv run niimbot print label.png -m b1 -c ble -a B1- -W 50 -H 30 --no-dither
uv run niimbot print label.png -W 50 -H 30 --preview out.png   # no printing
uv run niimbot status -m b1 -c ble
uv run niimbot scan
```

## Development

```sh
uv run pytest     # simulated printer, no hardware needed
```

## Credits

This project stands on the work of others. Thank you!

- **[niimprint](https://github.com/kjy00302/niimprint)** by [kjy00302](https://github.com/kjy00302): the original
  Python client that figured out the Niimbot protocol (MIT).
- **[niimprint fork](https://github.com/AndBondStyle/niimprint)** by [AndBondStyle](https://github.com/AndBondStyle):
  transport abstraction, multi-model support and cleanup. It is included (with fixes) in
  `src/niimbot_api/niimprint/` under its MIT license.
- **[NiimBlue / niimbluelib](https://github.com/MultiMote/niimblue)** by [MultiMote](https://github.com/MultiMote):
  its protocol research was the reference for the newer B1 print sequence and the BLE service.
- Built with [bleak](https://github.com/hbldh/bleak), [FastAPI](https://fastapi.tiangolo.com/),
  [Pillow](https://python-pillow.org/) and [pySerial](https://github.com/pyserial/pyserial).

## Disclaimer

This project is **not** an official Niimbot product. It is not affiliated with, endorsed by, sponsored by, or
supported by Niimbot (Wuhan Jingchen Intelligent Identification Technology Co., Ltd.). All product names,
trademarks and registered trademarks belong to their respective owners and are used here only to identify
compatible hardware.

The printer protocol comes from community reverse-engineering, not official documentation. It may stop working
after a printer firmware update. The software is provided "as is", without warranty of any kind (see
[LICENSE](LICENSE)). The authors are not responsible for any damage to your printer, labels or data. For official
software and support, use Niimbot's own apps.

## License

[MIT](LICENSE) © 2026 Hamza Boulila. The included niimprint code remains © 2023 kjy00302 under the MIT
license ([notice](src/niimbot_api/niimprint/LICENSE)).
