from __future__ import annotations

import fitz


def two_page_pdf_bytes(
    *,
    page_one_text: str = "Page one text for FinSight parser tests.",
    page_two_text: str = "Page two text for FinSight parser tests.",
) -> bytes:
    document = fitz.open()
    try:
        page_one = document.new_page()
        page_one.insert_text((72, 72), page_one_text)
        page_two = document.new_page()
        page_two.insert_text((72, 72), page_two_text)
        return document.tobytes()
    finally:
        document.close()
