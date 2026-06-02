from __future__ import annotations

from pathlib import Path
from typing import Any, TypedDict

import fitz

TEXT_PREVIEW_MAX_LEN = 120


class PdfParseError(Exception):
    def __init__(self, message: str, *, code: str = "parse_failed"):
        super().__init__(message)
        self.message = message
        self.code = code


class ParsedPage(TypedDict):
    page_number: int
    text: str
    text_length: int
    extraction_status: str


class PageMapEntry(TypedDict):
    page_number: int
    char_count: int
    has_text: bool
    text_preview: str


class PdfParseResult(TypedDict):
    page_count: int
    pages: list[ParsedPage]
    page_map_pages: list[PageMapEntry]


def _safe_text_preview(text: str) -> str:
    collapsed = " ".join(text.split())
    if len(collapsed) <= TEXT_PREVIEW_MAX_LEN:
        return collapsed
    return collapsed[:TEXT_PREVIEW_MAX_LEN]


class PdfParserService:
    def parse_pdf_file(self, pdf_path: Path) -> PdfParseResult:
        if not pdf_path.is_file():
            raise PdfParseError("PDF file is not available.", code="file_not_found")

        try:
            document = fitz.open(pdf_path)
        except Exception as exc:
            raise PdfParseError("Unable to read PDF file.", code="invalid_pdf") from exc

        try:
            page_count = document.page_count
            if page_count < 1:
                raise PdfParseError("PDF contains no pages.", code="empty_document")

            pages: list[ParsedPage] = []
            page_map_pages: list[PageMapEntry] = []
            for index in range(page_count):
                page = document.load_page(index)
                text = page.get_text("text") or ""
                text_length = len(text)
                extraction_status = "ok" if text.strip() else "empty"
                page_number = index + 1
                pages.append(
                    {
                        "page_number": page_number,
                        "text": text,
                        "text_length": text_length,
                        "extraction_status": extraction_status,
                    }
                )
                page_map_pages.append(
                    {
                        "page_number": page_number,
                        "char_count": text_length,
                        "has_text": bool(text.strip()),
                        "text_preview": _safe_text_preview(text),
                    }
                )

            if not any(page["has_text"] for page in page_map_pages):
                raise PdfParseError(
                    "PDF contains no extractable text.",
                    code="empty_document",
                )

            return {
                "page_count": page_count,
                "pages": pages,
                "page_map_pages": page_map_pages,
            }
        finally:
            document.close()

    def build_pages_payload(self, document_id: str, result: PdfParseResult) -> dict[str, Any]:
        return {
            "document_id": document_id,
            "page_count": result["page_count"],
            "pages": result["pages"],
        }

    def build_page_map_payload(
        self,
        *,
        document_id: str,
        file_name: str | None,
        file_hash: str | None,
        result: PdfParseResult,
    ) -> dict[str, Any]:
        return {
            "document_id": document_id,
            "file_name": file_name,
            "file_hash": file_hash,
            "page_count": result["page_count"],
            "pages": result["page_map_pages"],
        }
