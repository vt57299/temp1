from __future__ import annotations

from datetime import datetime
from typing import Any, Optional
from pydantic import BaseModel, Field


class DocumentRecord(BaseModel):
    document_id: str = Field(description="Stable identifier for the ingested document")
    created_at: datetime = Field(default_factory=datetime.utcnow)

    # Source information
    source: str = Field(description="Origin of the document, e.g. upload|webhook|email|folder")
    original_filename: str
    content_type: Optional[str] = None

    # Storage
    file_path: str = Field(description="Absolute path where the binary is stored")
    file_size_bytes: int

    # Extracted text
    text_char_count: int = 0
    text_excerpt: str = ""

    # Arbitrary metadata
    metadata: dict[str, Any] = Field(default_factory=dict)


class IngestionResponse(BaseModel):
    document_id: str
    message: str
    source: str


class WebhookPayload(BaseModel):
    source_url: Optional[str] = None
    content: Optional[str] = None
    filename: Optional[str] = None
    content_type: Optional[str] = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    def is_valid(self) -> bool:
        return bool(self.source_url or self.content)
