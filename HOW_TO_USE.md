# How to use niimbot-api

This guide covers how to start the print server, keep it running, and connect your apps to it.
The full API reference is in [README.md](README.md) and at `http://127.0.0.1:8000/docs` while the server runs.

---

## 1. Should I run it separately or put it inside my app?

**Run it separately (recommended).** Treat it like a printer driver. It runs on the computer that is
near the printer, and all your apps send print requests to it over HTTP.

```
 web app A ─┐
 web app B ─┼──HTTP──▶  niimbot-api  ──Bluetooth──▶  B1 printer
 script C  ─┘          (your Mac)
```

Why separate is usually better:

| | Separate service | Inside your app |
|---|---|---|
| Works with any language (JS, PHP, Python, …) | ✅ | ❌ only Python apps |
| Several apps can share the printer | ✅ the queue handles it | ❌ each app fights for the Bluetooth connection |
| Your app can be deployed on a server/cloud | ✅ the printer stays on your Mac | ❌ the app must run on the machine next to the printer |
| Survives app redeploys/restarts | ✅ | ❌ reconnects every time |
| Multiple workers (gunicorn, uvicorn --workers) | ✅ | ❌ only one process may hold the printer |

**Put it inside your app only if** your app is a Python backend (FastAPI, Flask, Django) that
runs **on the same computer as the printer** in a **single process**. See [section 5](#5-option-c--put-it-inside-a-python-app).

> Your app is a website in the browser (React, Vue, plain HTML…)? Keep the server separate
> and call it from the page (section 3). It takes about 3 lines of code.

---

## 2. Start the print server

### First time

```sh
cd ~/Documents/Projects/NiimBot-api
uv sync
```

Copy `.env.example` to `.env` and set your printer (find its name with `uv run niimbot scan`):

```ini
NIIMBOT_MODEL=b1
NIIMBOT_CONN=ble
NIIMBOT_ADDR=B1-XXXXXXXX
NIIMBOT_DENSITY=3
NIIMBOT_DITHER=false          # sharp edges: good for text, barcodes, QR codes
# NIIMBOT_LABEL_WIDTH_MM=40   # uncomment + set to your label roll size
# NIIMBOT_LABEL_HEIGHT_MM=30
```

**Set your label size here.** Then your apps can send just the image, without a size.

### Start it

```sh
uv run niimbot serve
```

Open **http://127.0.0.1:8000**. The test page lets you drop in an image, see the preview, and print.
If that works, the server side is done.

### Keep it running all the time (optional)

```sh
scripts/install-service.sh              # runs at login and restarts if it crashes
tail -f logs/server.log                 # see what it is doing
scripts/install-service.sh uninstall    # remove
```

> Things to know:
> - Keep the printer switched on and close the Niimbot phone app while the server is printing. Only one connection is possible at a time.
> - Don't click **Connect** in macOS Bluetooth settings. The server connects by itself.
> - The server lets go of the printer after 60 s of no jobs, so the phone app can use it again.

---

## 3. Option A: print from a web page (any framework)

This covers React, Vue, Svelte, Angular, plain HTML, and so on. The page talks to the print server directly.

### Load the client

```js
// Option 1: load it from the print server (nothing to install)
import { NiimbotClient } from "http://127.0.0.1:8000/niimbot-client.js";

// Option 2: copy src/niimbot_api/static/niimbot-client.js into your project
import { NiimbotClient } from "./niimbot-client.js";

const printer = new NiimbotClient("http://127.0.0.1:8000");
```

### Print something

```js
// a file from <input type="file">
await printer.print(input.files[0]);

// with options
await printer.print(input.files[0], {
  labelWidthMm: 40, labelHeightMm: 30,  // label size (skip if set in .env)
  dither: false,                         // false = sharp (text/barcodes), true = photos
  copies: 2,
  source: "order-1042",                  // your own reference, shows up in job history
});

// an image that is already online
await printer.print("https://myshop.com/labels/1042.png");

// a <canvas> you drew your label on (see below)
await printer.print(canvas);
```

Every call returns a job: `{ id, status: "done", ... }`. If something goes wrong, it throws a
`NiimbotError` with a readable `message`, for example "BLE scan could not find 'B1-XXXXXXXX'; is the printer on?".

### React example

```jsx
import { useState } from "react";
import { NiimbotClient } from "./niimbot-client.js";

const printer = new NiimbotClient("http://127.0.0.1:8000");

export function PrintButton({ file }) {
  const [state, setState] = useState("idle");

  async function handlePrint() {
    setState("printing");
    try {
      await printer.print(file, { labelWidthMm: 40, labelHeightMm: 30 });
      setState("done");
    } catch (e) {
      setState("error: " + e.message);
    }
  }

  return <button onClick={handlePrint} disabled={state === "printing"}>
    {state === "printing" ? "Printing…" : "Print label"}
  </button>;
}
```

### Build the label in your app (text + barcode) and print it

Draw the label on a canvas at **8 pixels per mm** (a 40×30 mm label is 320×240 px), then print the canvas.
This example uses [JsBarcode](https://github.com/lindell/JsBarcode) for the barcode:

```html
<script src="https://cdn.jsdelivr.net/npm/jsbarcode@3/dist/JsBarcode.all.min.js"></script>
<script type="module">
import { NiimbotClient } from "http://127.0.0.1:8000/niimbot-client.js";
const printer = new NiimbotClient("http://127.0.0.1:8000");

function makeLabel({ name, specs, price, code }) {
  const W = 40 * 8, H = 30 * 8;                     // 40x30 mm
  const canvas = document.createElement("canvas");
  canvas.width = W; canvas.height = H;
  const ctx = canvas.getContext("2d");
  ctx.fillStyle = "#fff"; ctx.fillRect(0, 0, W, H); // white background

  const bars = document.createElement("canvas");
  JsBarcode(bars, code, { displayValue: false, margin: 0, height: 90, width: 2 });
  ctx.drawImage(bars, (W - bars.width) / 2, 8);

  ctx.fillStyle = "#000"; ctx.textAlign = "center";
  ctx.font = "bold 20px sans-serif"; ctx.fillText(name, W / 2, 130);
  ctx.font = "16px sans-serif";      ctx.fillText(specs, W / 2, 155);
  ctx.font = "bold 22px sans-serif"; ctx.fillText(price, W / 2, 190);
  return canvas;
}

document.querySelector("#print").onclick = () =>
  printer.print(makeLabel({
    name: 'Apple MacBook Pro 16"', specs: "M4 Max - 36 Go RAM - 1 To",
    price: "33499.00 DH", code: "1234567890",
  }), { dither: false });
</script>
<button id="print">Print price tag</button>
```

Tip: call `printer.preview(canvas)` to get the exact black-and-white image as a Blob before printing.

### When the web page is on a real domain (not localhost)

Pages on `localhost` are always allowed. For your deployed site, add its address to `.env` and restart the server:

```ini
NIIMBOT_CORS_ORIGINS=https://myshop.com,https://admin.myshop.com
```

Any website not on this list gets `403 Forbidden`, so a random site you visit can't use your printer.
Chrome may ask once for permission to access devices on your local network. Click **Allow**.

---

## 4. Option B: print from your backend

Use this when your server decides what to print, for example printing a label automatically when an order comes in.
**Your backend must be able to reach the print server:** either it runs on the same Mac, or you use a tunnel (see section 6).

Protect the print server with a key first. Put this in `.env` and restart:

```ini
NIIMBOT_API_KEY=pick-a-long-random-string
```

Send the key as the `X-API-Key` header in every request.

### Node.js / Express (Node 18+)

```js
import fs from "node:fs/promises";

async function printLabel(pngBuffer, options = {}) {
  const form = new FormData();
  form.append("file", new Blob([pngBuffer], { type: "image/png" }), "label.png");
  for (const [k, v] of Object.entries(options)) form.append(k, String(v));

  const res = await fetch("http://127.0.0.1:8000/v1/print", {
    method: "POST",
    headers: { "X-API-Key": process.env.PRINTER_API_KEY },
    body: form,
  });
  const job = await res.json();
  if (!res.ok) throw new Error(job.error || JSON.stringify(job.detail));
  return job;
}

await printLabel(await fs.readFile("label.png"), { label_width_mm: 40, label_height_mm: 30 });
```

### Next.js: forward prints from the browser through your backend

This keeps the API key secret, so the browser never sees it.

```ts
// app/api/print/route.ts
export async function POST(req: Request) {
  const form = await req.formData();              // the browser sends { file, ...options }
  const res = await fetch(`${process.env.PRINTER_URL}/v1/print`, {
    method: "POST",
    headers: { "X-API-Key": process.env.PRINTER_API_KEY! },
    body: form,
  });
  return new Response(res.body, { status: res.status, headers: { "Content-Type": "application/json" } });
}
```

### Python (requests)

```python
import requests

with open("label.png", "rb") as f:
    r = requests.post(
        "http://127.0.0.1:8000/v1/print",
        headers={"X-API-Key": "pick-a-long-random-string"},
        files={"file": f},
        data={"label_width_mm": 40, "label_height_mm": 30, "copies": 1},
        timeout=90,
    )
r.raise_for_status()
print(r.json()["status"])   # "done"
```

### PHP / Laravel

```php
$ch = curl_init("http://127.0.0.1:8000/v1/print");
curl_setopt_array($ch, [
    CURLOPT_POST => true,
    CURLOPT_RETURNTRANSFER => true,
    CURLOPT_HTTPHEADER => ["X-API-Key: " . env("PRINTER_API_KEY")],
    CURLOPT_POSTFIELDS => [
        "file" => new CURLFile("/path/label.png", "image/png"),
        "label_width_mm" => 40,
        "label_height_mm" => 30,
    ],
]);
$job = json_decode(curl_exec($ch), true);   // $job["status"] === "done"
```

```php
// Laravel HTTP client
Http::withHeaders(["X-API-Key" => env("PRINTER_API_KEY")])
    ->attach("file", file_get_contents($path), "label.png")
    ->post("http://127.0.0.1:8000/v1/print", ["label_width_mm" => 40, "label_height_mm" => 30]);
```

### Just send a link to an image

If your app already produces label images at a URL:

```sh
curl -H "X-API-Key: …" -H "Content-Type: application/json" \
     -d '{"image_url": "https://myshop.com/labels/1042.png", "copies": 1}' \
     http://127.0.0.1:8000/v1/print/url
```

### Don't make users wait

Printing takes about 3 to 6 seconds. To respond right away, send `wait=false` and check the job later:

```js
const job = await printer.print(file, { wait: false });   // status: "queued"
// ...later
const done = await printer.job(job.id, 30);              // waits up to 30 s for it to finish
```

---

## 5. Option C: put it inside a Python app

Only use this when your Python app runs on the Mac next to the printer, as **one process**.

### Install it into your project

```sh
cd ~/path/to/your-app
uv add --editable ~/Documents/Projects/NiimBot-api
# or: pip install -e ~/Documents/Projects/NiimBot-api
```

### FastAPI: add the whole print API under `/printer`

```python
from contextlib import asynccontextmanager
from fastapi import FastAPI
from niimbot_api.app import create_app
from niimbot_api.config import Settings

printer_app = create_app(Settings(model="b1", conn="ble", addr="B1-XXXXXXXX",
                                  label_width_mm=40, label_height_mm=30, dither=False))

@asynccontextmanager
async def lifespan(app):
    yield
    printer_app.state.jobs.stop()        # mounted apps don't get shutdown events,
    printer_app.state.service.close()    # so release the printer here

app = FastAPI(lifespan=lifespan)
app.mount("/printer", printer_app)       # → /printer/v1/print, /printer/docs, /printer/ (test page)
```

Run it with **one** worker: `uvicorn main:app` (not `--workers 4`).

### Any Python code (Flask, Django, scripts): call it directly as a library

```python
from PIL import Image
from niimbot_api import ImageOptions, PrinterConfig, PrinterService, PrintJob

# create ONCE when your app starts, reuse everywhere
printer = PrinterService(
    PrinterConfig(model="b1", conn="ble", addr="B1-XXXXXXXX", density=3),
    keepalive_seconds=60,   # stay connected between prints
)

def print_label(path: str, copies: int = 1):
    job = PrintJob(
        Image.open(path),
        ImageOptions(label_width_mm=40, label_height_mm=30, dither=False),
        copies=copies,
    )
    return printer.print(job)   # blocks ~3-6 s; thread-safe (one print at a time)

print_label("label.png")
```

---

## 6. Where is your app running?

| Your app runs… | What to do |
|---|---|
| On the same Mac (`localhost:3000` etc.) | Nothing, it just works |
| As a website on the internet, printing from **your browser** on this Mac | Add the site to `NIIMBOT_CORS_ORIGINS` (section 3) |
| On another computer/phone on the **same Wi-Fi** | In `.env`: `NIIMBOT_HOST=0.0.0.0` + `NIIMBOT_API_KEY=…`; call `http://<your-mac-ip>:8000` (find the IP with `ipconfig getifaddr en0`) |
| On a **cloud server** / **online app** | Expose the print server with Tailscale Funnel or Cloudflare Tunnel (see below) |

### Exposing your server online with Tailscale Funnel

If your website or backend runs on the internet (e.g., Vercel, Netlify, AWS) and needs to send print jobs to your local Mac over HTTPS:

> ⚠️ Funnel makes the print server reachable by **anyone on the internet**. Set `NIIMBOT_API_KEY` in `.env` first,
> and if browsers on your site call it directly, add the site to `NIIMBOT_CORS_ORIGINS`.

1. **Install Tailscale CLI**:
   ```sh
   brew install tailscale
   ```

2. **Start the Tailscale service & authenticate**:
   ```sh
   sudo brew services start tailscale
   sudo tailscale up
   ```
   *(Open the authentication link in your browser to log in)*

3. **Expose your print server (port 8000)**:
   ```sh
   tailscale funnel --bg 8000
   ```
   *(If prompted, click the link to enable Funnel in your Tailscale admin console)*

4. **Use your public HTTPS URL**:
   Tailscale provides a secure HTTPS URL (e.g. `https://your-mac.tail1234.ts.net`). Use this URL as your `PRINTER_URL` or in your JavaScript client (`new NiimbotClient("https://your-mac.tail1234.ts.net", { apiKey: "..." })`).

---

## 7. Making labels that print well

- **Size:** 8 pixels per mm. For example, 40×30 mm is 320×240 px and 50×30 mm is 400×240 px. The B1 prints at most 384 px (48 mm) across.
- **Width** means the side that runs across the print head, as the label comes out of the printer. If your label comes out sideways, add `rotate: 90`.
- **Black on white only.** Colours and greys get converted:
  - `dither: false` gives crisp edges. Use it for text, barcodes and QR codes.
  - `dither: true` gives dot shading. Use it for photos and logos with gradients.
- **Transparent backgrounds** print as white. That's fine.
- **Check before printing.** Use `printer.preview(...)`, `POST /v1/preview`, or the test page at `/`. You'll see exactly what the printer will get.
- **Faint print?** Set `density: 4` or `5`.

---

## 8. Troubleshooting

| Problem | Fix |
|---|---|
| `Cannot reach print server` | Server isn't running. Start `uv run niimbot serve`, or check `logs/server.log` |
| `BLE scan could not find 'B1-…'` (503) | Printer off/asleep, out of range, or connected to the phone app. Close the app and switch the printer off and on |
| `403 Origin … is not allowed` | Add your site's address to `NIIMBOT_CORS_ORIGINS` and restart |
| `401 Invalid or missing API key` | Send `X-API-Key` header (or `Authorization: Bearer …`) |
| `422 Image is 400px wide but B1 prints at most 384px` | Pass `label_width_mm`/`fit`, or make the image ≤ 384 px wide |
| Label prints sideways | `rotate: 90` (or 270) |
| Label is cut off / shifted | Your `label_width_mm`/`label_height_mm` don't match the roll |
| Works in terminal but not as a background service | System Settings › Privacy & Security › Bluetooth → allow the Python app, or run in a terminal |
| Browser shows "mixed content" / blocked (Safari) | Use Chrome, or put the server behind an https tunnel (section 6) |

Useful checks:

```sh
curl http://127.0.0.1:8000/health             # is the server up?
curl http://127.0.0.1:8000/v1/printer         # config + queue + connected?
curl http://127.0.0.1:8000/v1/printer/status  # battery, label roll (talks to the printer)
curl http://127.0.0.1:8000/v1/jobs            # recent prints and errors
uv run niimbot scan                           # can this Mac see the printer?
```
