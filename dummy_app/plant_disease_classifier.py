"""
plant_disease_classifier.py

Plant disease classifier (FastAPI) + simple web UI at /ui

Modes:
- ok               -> resizes image if needed, returns dummy prediction (200)
- wrong_size       -> always throws 422 IMAGE_SIZE_INVALID
- latency          -> sleeps to simulate slow inference (200)
- typo_exception   -> throws 400 and logs 'eexception'
- loop             -> simulates hang then 504, logs ERROR/FATAL

Run:
  pip install fastapi uvicorn pillow python-multipart
  uvicorn plant_disease_classifier:app --reload --port 8000

UI:
  http://127.0.0.1:8000/ui
Docs:
  http://127.0.0.1:8000/docs
"""

from __future__ import annotations

import asyncio
import io
import logging
import random
import time
import uuid
from typing import Literal

from fastapi import FastAPI, File, HTTPException, Query, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse
from PIL import Image

app = FastAPI(title="Plant Disease Classifier", version="1.0.0")

# -------------------------
# Plain-text logs (CloudWatch style)
# -------------------------
logger = logging.getLogger("plant_classifier")
logger.setLevel(logging.INFO)
handler = logging.StreamHandler()
handler.setFormatter(logging.Formatter("%(message)s"))
logger.handlers = [handler]


def log_event(level: Literal["INFO", "WARN", "ERROR", "FATAL"], **fields) -> None:
    """
    Emits logs like:
      ERROR request_id=req-xxxx endpoint=/classify stage=preprocess error_code=... message="..."
    """
    request_id = fields.pop("request_id", "-")
    endpoint = fields.pop("endpoint", "-")
    stage = fields.pop("stage", "-")

    kv_parts = []
    for k, v in fields.items():
        if isinstance(v, str):
            if any(ch in v for ch in [' ', '"', "'"]):
                v = v.replace('"', '\\"')
                kv_parts.append(f'{k}="{v}"')
            else:
                kv_parts.append(f"{k}={v}")
        else:
            kv_parts.append(f"{k}={v}")

    line = f"{level} request_id={request_id} endpoint={endpoint} stage={stage}"
    if kv_parts:
        line += " " + " ".join(kv_parts)

    if level == "INFO":
        logger.info(line)
    elif level == "WARN":
        logger.warning(line)
    elif level == "ERROR":
        logger.error(line)
    else:
        logger.critical(line)


# -------------------------
# Helpers
# -------------------------
def open_image_or_400(file_bytes: bytes) -> Image.Image:
    try:
        return Image.open(io.BytesIO(file_bytes)).convert("RGB")
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid image: {e}")


async def fake_inference(sleep_ms: int = 0) -> dict:
    if sleep_ms > 0:
        await asyncio.sleep(sleep_ms / 1000.0)
    label = random.choice(["healthy", "rust", "blight", "mildew"])
    confidence = round(random.uniform(0.65, 0.95), 2)
    return {"label": label, "confidence": confidence}


# -------------------------
# UI (simple HTML page)
# -------------------------
@app.get("/ui", response_class=HTMLResponse)
def ui():
    return """
<!doctype html>
<html>
  <head>
    <meta charset="utf-8" />
    <title>Shitty Leaf Classifier UI</title>
    <style>
      body { font-family: Arial, sans-serif; max-width: 780px; margin: 40px auto; }
      .card { padding: 16px; border: 1px solid #ddd; border-radius: 10px; }
      label { display: block; margin-top: 12px; font-weight: 600; }
      select, input[type=file], input[type=number] { width: 100%; padding: 8px; margin-top: 6px; }
      button { margin-top: 14px; padding: 10px 14px; border: 0; border-radius: 8px; cursor: pointer; }
      pre { white-space: pre-wrap; word-wrap: break-word; background: #f7f7f7; padding: 12px; border-radius: 8px; }
      .row { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }
      .muted { color: #666; font-size: 14px; }
    </style>
  </head>

  <body>
    <h2>Plant Disease Classifier</h2>
    <p class="muted">
      Upload a leaf image, select a mode to trigger incidents. Logs appear in the terminal where uvicorn is running.
      Docs: <a href="/docs">/docs</a> | Health: <a href="/health">/health</a>
    </p>

    <div class="card">
      <label>Mode</label>
      <select id="mode">
        <option value="ok">ok (200)</option>
        <option value="wrong_size">wrong_size (422)</option>
        <option value="latency">latency (slow, 200)</option>
        <option value="typo_exception">typo_exception (400)</option>
        <option value="loop">loop (504)</option>
      </select>

      <div class="row">
        <div>
          <label>latency_ms (only for latency)</label>
          <input id="latency_ms" type="number" value="6000" min="0" step="100" />
        </div>
        <div>
          <label>loop_seconds (only for loop)</label>
          <input id="loop_seconds" type="number" value="10" min="1" step="1" />
        </div>
      </div>

      <label>Image</label>
      <input id="file" type="file" accept="image/*" />

      <button id="run">Upload & Classify</button>
    </div>

    <h3>Result</h3>
    <div class="card">
      <div id="status" class="muted">Status: (none)</div>
      <pre id="output">{}</pre>
    </div>

    <script>
      const runBtn = document.getElementById("run");
      const out = document.getElementById("output");
      const statusEl = document.getElementById("status");

      runBtn.addEventListener("click", async () => {
        const mode = document.getElementById("mode").value;
        const latencyMs = document.getElementById("latency_ms").value;
        const loopSeconds = document.getElementById("loop_seconds").value;
        const fileInput = document.getElementById("file");

        if (!fileInput.files || fileInput.files.length === 0) {
          alert("Please choose an image file.");
          return;
        }

        const fd = new FormData();
        fd.append("file", fileInput.files[0]);

        const qs = new URLSearchParams();
        qs.set("mode", mode);
        qs.set("latency_ms", latencyMs);
        qs.set("loop_seconds", loopSeconds);

        statusEl.textContent = "Status: running...";
        out.textContent = "";

        try {
          const res = await fetch(`/classify?${qs.toString()}`, {
            method: "POST",
            body: fd
          });

          const text = await res.text();
          statusEl.textContent = `Status: ${res.status} ${res.statusText}`;

          try {
            const obj = JSON.parse(text);
            out.textContent = JSON.stringify(obj, null, 2);
          } catch {
            out.textContent = text;
          }
        } catch (e) {
          statusEl.textContent = "Status: error";
          out.textContent = String(e);
        }
      });
    </script>
  </body>
</html>
"""


# -------------------------
# API routes
# -------------------------
@app.get("/")
def root():
    return {"ok": True, "docs": "/docs", "ui": "/ui", "health": "/health"}


@app.get("/health")
def health():
    return {"ok": True}


@app.post("/classify")
async def classify(
    file: UploadFile = File(...),
    mode: Literal["ok", "wrong_size", "latency", "loop", "typo_exception"] = Query("ok"),
    expected_w: int = Query(224),
    expected_h: int = Query(224),
    latency_ms: int = Query(6000),
    loop_seconds: int = Query(10),
):
    request_id = f"req-{uuid.uuid4().hex[:8]}"
    endpoint = "/classify"

    # Upload
    log_event("INFO", request_id=request_id, endpoint=endpoint, stage="upload", filename=file.filename)
    file_bytes = await file.read()
    log_event("INFO", request_id=request_id, endpoint=endpoint, stage="upload", bytes=len(file_bytes))

    # Decode
    img = open_image_or_400(file_bytes)
    w, h = img.size
    log_event(
        "INFO",
        request_id=request_id,
        endpoint=endpoint,
        stage="preprocess",
        width=f"{w}x{h}",
        expected=f"{expected_w}x{expected_h}",
        mode=mode,
    )

    # WRONG SIZE incident (only if mode=wrong_size)
    if mode == "wrong_size":
        log_event(
            "ERROR",
            request_id=request_id,
            endpoint=endpoint,
            stage="preprocess",
            error_code="IMAGE_SIZE_INVALID",
            expected=f"{expected_w}x{expected_h}",
            got=f"{w}x{h}",
            message="ValueError: invalid image shape (expected 224x224x3)",
        )
        raise HTTPException(
            status_code=422,
            detail={
                "error": "IMAGE_SIZE_INVALID",
                "expected": {"width": expected_w, "height": expected_h},
                "got": {"width": w, "height": h},
                "request_id": request_id,
            },
        )

    # For other modes: resize to allow latency/loop/typo to happen
    if (w != expected_w) or (h != expected_h):
        log_event(
            "WARN",
            request_id=request_id,
            endpoint=endpoint,
            stage="preprocess",
            message="image size mismatch; resizing",
            got=f"{w}x{h}",
            resized_to=f"{expected_w}x{expected_h}",
        )
        img = img.resize((expected_w, expected_h))

    # LATENCY incident
    if mode == "latency":
        log_event(
            "WARN",
            request_id=request_id,
            endpoint=endpoint,
            stage="inference",
            message="Latency threshold exceeded; model inference is slow",
            latency_ms=latency_ms,
            threshold_ms=2000,
        )
        result = await fake_inference(sleep_ms=latency_ms)
        log_event(
            "INFO",
            request_id=request_id,
            endpoint=endpoint,
            stage="postprocess",
            prediction=result["label"],
            confidence=result["confidence"],
        )
        return JSONResponse({"request_id": request_id, "mode": mode, "result": result})

    # LOOP/HANG incident
    if mode == "loop":
        log_event(
            "ERROR",
            request_id=request_id,
            endpoint=endpoint,
            stage="handler",
            message="suspected infinite loop: while True entered",
        )
        start = time.time()
        iteration = 0
        while time.time() - start < loop_seconds:
            iteration += 1
            if iteration % 200000 == 0:
                log_event(
                    "ERROR",
                    request_id=request_id,
                    endpoint=endpoint,
                    stage="handler",
                    message="heartbeat still running",
                    iteration=iteration,
                )
            if iteration % 50000 == 0:
                await asyncio.sleep(0)

        log_event(
            "FATAL",
            request_id=request_id,
            endpoint=endpoint,
            stage="handler",
            message="watchdog timeout; aborting request",
        )
        raise HTTPException(
            status_code=504,
            detail={"error": "REQUEST_TIMEOUT", "request_id": request_id, "mode": mode},
        )

    # TYPO EXCEPTION incident
    if mode == "typo_exception":
        log_event(
            "ERROR",
            request_id=request_id,
            endpoint=endpoint,
            stage="preprocess",
            error_code="DECODE_FAILED",
            message="eexception: failed to decode image bytes",
        )
        raise HTTPException(
            status_code=400,
            detail={"error": "DECODE_FAILED", "request_id": request_id, "mode": mode},
        )

    # OK
    log_event("INFO", request_id=request_id, endpoint=endpoint, stage="inference", message="running inference")
    result = await fake_inference(sleep_ms=250)
    log_event(
        "INFO",
        request_id=request_id,
        endpoint=endpoint,
        stage="postprocess",
        prediction=result["label"],
        confidence=result["confidence"],
    )
    return JSONResponse({"request_id": request_id, "mode": mode, "result": result})
