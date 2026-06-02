from __future__ import annotations

from typing import Any

from app.models.document import Document
from app.models.document_chunk import ChunkType, DocumentChunkCreate
from app.services.pdf_parser_service import TEXT_PREVIEW_MAX_LEN

CHUNK_MAX_CHARS = 3500
CHUNK_OVERLAP_CHARS = 300


def _safe_text_preview(text: str) -> str:
    collapsed = " ".join(text.split())
    if len(collapsed) <= TEXT_PREVIEW_MAX_LEN:
        return collapsed
    return collapsed[:TEXT_PREVIEW_MAX_LEN]


def _split_page_text(text: str) -> list[str]:
    stripped = text.strip()
    if not stripped:
        return []
    if len(stripped) <= CHUNK_MAX_CHARS:
        return [stripped]

    segments: list[str] = []
    start = 0
    while start < len(stripped):
        end = min(start + CHUNK_MAX_CHARS, len(stripped))
        segment = stripped[start:end].strip()
        if segment:
            segments.append(segment)
        if end >= len(stripped):
            break
        start = max(end - CHUNK_OVERLAP_CHARS, start + 1)
    return segments


def build_chunks_from_pages(
    document: Document,
    pages_payload: dict[str, Any],
) -> list[DocumentChunkCreate]:
    payload_document_id = pages_payload.get("document_id")
    if payload_document_id and payload_document_id != document.document_id:
        raise ValueError("Parsed pages document_id does not match document.")

    document_type = (
        document.document_type.value
        if hasattr(document.document_type, "value")
        else str(document.document_type)
    )
    chunks: list[DocumentChunkCreate] = []
    chunk_index = 0

    for page in pages_payload.get("pages", []):
        if page.get("extraction_status") != "ok":
            continue
        page_number = int(page["page_number"])
        for segment in _split_page_text(page.get("text", "")):
            chunk_id = f"{document.document_id}_chunk_{chunk_index:04d}"
            chunks.append(
                DocumentChunkCreate(
                    chunk_id=chunk_id,
                    document_id=document.document_id,
                    company_id=document.company_id,
                    document_type=document_type,
                    fiscal_year=document.fiscal_year,
                    quarter=document.quarter,
                    period=document.period,
                    page_start=page_number,
                    page_end=page_number,
                    page_numbers=[page_number],
                    section_title=None,
                    chunk_index=chunk_index,
                    chunk_type=ChunkType.TEXT,
                    text=segment,
                    text_preview=_safe_text_preview(segment),
                    char_count=len(segment),
                    word_count=len(segment.split()),
                    source_document_title=document.title,
                    source_url=document.source_url,
                    file_hash=document.file_hash,
                )
            )
            chunk_index += 1

    if not chunks:
        raise ValueError("No chunkable text found in parsed pages.")
    return chunks
