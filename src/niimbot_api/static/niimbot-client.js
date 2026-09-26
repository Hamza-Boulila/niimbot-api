/**
 * Tiny browser/Node client for the Niimbot Print API. No dependencies.
 *
 *   import { NiimbotClient } from "http://127.0.0.1:8000/niimbot-client.js";
 *   const printer = new NiimbotClient("http://127.0.0.1:8000", { apiKey: "..." });
 *   await printer.print(fileOrBlobOrCanvasOrUrl, { labelWidthMm: 50, labelHeightMm: 30 });
 *
 * Options (all optional): labelWidthMm, labelHeightMm, fit ("contain"|"cover"|"stretch"|"none"),
 * rotate (0|90|180|270), dither (bool), threshold (0-255), invert (bool), density (1-5),
 * copies (1-100), source (your reference string), wait (default true), waitTimeout (seconds).
 */

const FIELD_NAMES = {
  labelWidthMm: "label_width_mm",
  labelHeightMm: "label_height_mm",
  waitTimeout: "wait_timeout",
};

export class NiimbotError extends Error {
  constructor(message, status, body) {
    super(message);
    this.name = "NiimbotError";
    this.status = status;
    this.body = body;
  }
}

export class NiimbotClient {
  constructor(baseUrl = "http://127.0.0.1:8000", { apiKey } = {}) {
    this.baseUrl = baseUrl.replace(/\/$/, "");
    this.apiKey = apiKey;
  }

  /**
   * Print an image. `image` can be a File/Blob, an HTMLCanvasElement/OffscreenCanvas,
   * a data: URL, or an http(s) URL the server can download.
   * Resolves with the job once printed (or queued, when wait: false).
   */
  async print(image, options = {}) {
    const opts = toFields(options);
    if (typeof image === "string") {
      if (image.startsWith("data:")) {
        return this._json("/v1/print/base64", { ...opts, image_base64: image });
      }
      return this._json("/v1/print/url", { ...opts, image_url: image });
    }
    const form = toForm(await toBlob(image), opts);
    return this._request("/v1/print", { method: "POST", body: form });
  }

  /** The exact black & white bitmap the printer would get, as a PNG Blob. */
  async preview(image, options = {}) {
    const form = toForm(await toBlob(image), toFields(options));
    return this._request("/v1/preview", { method: "POST", body: form }, "blob");
  }

  /** Job status; pass waitSeconds to long-poll until it finishes. */
  job(id, waitSeconds = 0) {
    return this._request(`/v1/jobs/${encodeURIComponent(id)}?wait=${waitSeconds}`);
  }

  jobs(limit = 50) {
    return this._request(`/v1/jobs?limit=${limit}`);
  }

  /** Configured model, label defaults and queue length (fast, no printer I/O). */
  printer() {
    return this._request("/v1/printer");
  }

  /** Battery, firmware and loaded label, read from the printer. */
  status() {
    return this._request("/v1/printer/status");
  }

  health() {
    return this._request("/health");
  }

  _json(path, body) {
    return this._request(path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
  }

  async _request(path, init = {}, as = "json") {
    const headers = new Headers(init.headers);
    if (this.apiKey) headers.set("X-API-Key", this.apiKey);
    let res;
    try {
      res = await fetch(this.baseUrl + path, { ...init, headers });
    } catch (e) {
      throw new NiimbotError(`Cannot reach print server at ${this.baseUrl}: ${e.message}`, 0);
    }
    if (as === "blob" && res.ok) return res.blob();
    const body = await res.json().catch(() => null);
    if (!res.ok) {
      const msg = body?.error || formatDetail(body?.detail) || res.statusText;
      throw new NiimbotError(msg, res.status, body);
    }
    return body;
  }
}

function toFields(options) {
  const out = {};
  for (const [key, value] of Object.entries(options)) {
    if (value === undefined || value === null) continue;
    out[FIELD_NAMES[key] ?? key] = value;
  }
  return out;
}

function toForm(blob, fields) {
  const form = new FormData();
  form.append("file", blob, blob.name || "label.png");
  for (const [key, value] of Object.entries(fields)) form.append(key, String(value));
  return form;
}

async function toBlob(image) {
  if (image instanceof Blob) return image;
  if (typeof OffscreenCanvas !== "undefined" && image instanceof OffscreenCanvas) {
    return image.convertToBlob({ type: "image/png" });
  }
  if (image && typeof image.toBlob === "function") {
    return new Promise((resolve, reject) =>
      image.toBlob((b) => (b ? resolve(b) : reject(new Error("Canvas is empty"))), "image/png"),
    );
  }
  throw new TypeError("image must be a File, Blob, canvas, data: URL or http(s) URL");
}

function formatDetail(detail) {
  if (!detail) return null;
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) return detail.map((d) => `${d.loc?.slice(-1)[0]}: ${d.msg}`).join("; ");
  return JSON.stringify(detail);
}
