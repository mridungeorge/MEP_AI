"""Render the pages of a PDF to images for the vision reader (pdfium via pypdfium2), and read each page's text layer.

Run ONLY inside the sandboxed worker (`python -m mep.ingest.worker pdf-render`), because a PDF is untrusted input to a C library. Pages are
rendered in greyscale (a plan is line work) at a bounded size, and encoded as PNG with the standard library so the bytes depend only on
the pixels. Nothing here reads a value from the drawing: it only produces pictures and text for the vision step, whose output is evidence.
"""
import struct
import zlib
from typing import Any

MAX_PAGES = 30
TARGET_DPI = 110
MAX_SIDE_PX = 2400
MAX_PAGE_POINTS = 14_400.0          # 200 inches: a larger page is refused, not rendered
MAX_TEXT_CHARS = 4000


class PdfRenderError(Exception):
    """The file is not a PDF this server will render."""


def _png_gray(width: int, height: int, stride: int, data: bytes) -> bytes:
    raw = bytearray()
    for y in range(height):
        raw += b"\x00" + data[y * stride:y * stride + width]

    def chunk(kind: bytes, body: bytes) -> bytes:
        return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body) & 0xFFFFFFFF)

    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 0, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(bytes(raw), 6)) + chunk(b"IEND", b""))


def render_pdf(path: str, max_pages: int = MAX_PAGES) -> dict[str, Any]:
    """{'total_pages', 'rendered', 'pages': [{'number', 'width', 'height', 'png', 'text'}], 'problems': [...]}"""
    import pypdfium2 as pdfium
    from pypdfium2 import PdfiumError

    try:
        pdf = pdfium.PdfDocument(path)
    except (PdfiumError, OSError, ValueError, RuntimeError) as exc:
        raise PdfRenderError(f"not a readable PDF: {type(exc).__name__}") from None
    total = len(pdf)
    if total == 0:
        raise PdfRenderError("the PDF has no pages")
    problems: list[str] = []
    pages: list[dict[str, Any]] = []
    for i in range(min(total, max_pages)):
        page = pdf[i]
        try:
            w_pt, h_pt = page.get_size()
            if not (0 < w_pt <= MAX_PAGE_POINTS and 0 < h_pt <= MAX_PAGE_POINTS):
                problems.append(f"page {i + 1}: size {w_pt:g} x {h_pt:g} pt is outside what is rendered, skipped")
                continue
            scale = min(TARGET_DPI / 72.0, MAX_SIDE_PX / max(w_pt, h_pt))
            bitmap = page.render(scale=scale, grayscale=True)
            w, h = bitmap.width, bitmap.height
            png = _png_gray(w, h, bitmap.stride, bytes(bitmap.buffer))
            try:
                text = page.get_textpage().get_text_range()[:MAX_TEXT_CHARS]
            except Exception:  # noqa: BLE001 - a page without a usable text layer is still rendered
                text = ""
            pages.append({"number": i + 1, "width": w, "height": h, "png": png, "text": " ".join(text.split())})
        except PdfiumError as exc:
            problems.append(f"page {i + 1}: could not be rendered ({type(exc).__name__})")
        finally:
            page.close()
    if total > max_pages:
        problems.append(f"the PDF has {total} pages: only the first {max_pages} were rendered")
    return {"total_pages": total, "rendered": len(pages), "pages": pages, "problems": problems}
