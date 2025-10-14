from __future__ import annotations

import base64
from typing import Optional

from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.responses import JSONResponse

from .models import IngestionResponse, WebhookPayload
from .ingestion.agent import ingest_bytes

app = FastAPI(title="RFP Scout Input Layer", version="0.1.0")


@app.get("/healthz")
def healthz():
    return {"status": "ok"}


@app.post("/ingest/upload", response_model=IngestionResponse)
async def ingest_upload(
    file: UploadFile = File(...),
    source: str = Form("upload"),
):
    try:
        content = await file.read()
        record = ingest_bytes(
            content=content,
            original_filename=file.filename or "upload.bin",
            source=source,
            content_type=file.content_type,
        )
        return IngestionResponse(
            document_id=record.document_id,
            message="Ingestion successful",
            source=record.source,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/ingest/webhook", response_model=IngestionResponse)
async def ingest_webhook(payload: WebhookPayload):
    if not payload.is_valid():
        raise HTTPException(status_code=400, detail="Provide content or source_url")

    content_bytes: Optional[bytes] = None
    filename = payload.filename or "webhook.txt"

    if payload.content:
        try:
            # Allow raw text or base64-encoded binary
            if payload.content.startswith("data:"):
                b64 = payload.content.split(",", 1)[1]
                content_bytes = base64.b64decode(b64)
            else:
                content_bytes = payload.content.encode("utf-8")
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid content format")

    if content_bytes is None:
        # For brevity, we do not implement fetching from source_url here
        raise HTTPException(status_code=400, detail="Fetching from source_url not implemented yet")

    record = ingest_bytes(
        content=content_bytes,
        original_filename=filename,
        source="webhook",
        content_type=payload.content_type,
        metadata=payload.metadata,
    )
    return IngestionResponse(
        document_id=record.document_id,
        message="Ingestion successful",
        source=record.source,
    )
