#!/usr/bin/env python3
"""
process_pdfs.py — Combine, OCR, and organize PDF files.

Usage:
    python process_pdfs.py FILE_PATTERNS [--out OUTPUT] [--ocr] [--gen_toc]

FILE_PATTERNS is a comma-separated list of PDF paths or glob patterns.
"""

import argparse
import glob
import os
import shutil
import sys
import tempfile
from pathlib import Path


# ---------------------------------------------------------------------------
# Windows: auto-add OCR tools to PATH if installed but not on PATH
# ---------------------------------------------------------------------------

def _add_to_path_if_found(exe_name: str, candidates: list[str], label: str) -> bool:
    """Try candidate directories for exe_name; add the first match to PATH.

    Returns True if the executable was already on PATH or was found and added.
    """
    if shutil.which(exe_name):
        return True
    if sys.platform != "win32":
        return False
    for directory in candidates:
        if os.path.isfile(os.path.join(directory, exe_name)):
            os.environ["PATH"] = os.environ["PATH"] + os.pathsep + directory
            print(f"Info: added {label} to PATH from {directory}", file=sys.stderr)
            return True
    return False


def _ensure_tesseract_on_path() -> None:
    """On Windows, add Tesseract to PATH if installed but not found on PATH."""
    _add_to_path_if_found(
        "tesseract.exe",
        [
            r"C:\Program Files\Tesseract-OCR",
            r"C:\Program Files (x86)\Tesseract-OCR",
            os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs", "Tesseract-OCR"),
        ],
        "Tesseract",
    )


def _ensure_ghostscript_on_path() -> None:
    """On Windows, add Ghostscript to PATH if installed but not found on PATH.

    Ghostscript is used by ocrmypdf for PDF optimisation; without it ocrmypdf
    still works but emits many [WinError 2] probe warnings.
    """
    if shutil.which("gswin64c") or shutil.which("gs"):
        return
    if sys.platform != "win32":
        return

    # Ghostscript installs under C:\Program Files\gs\gs<version>\bin\
    gs_glob = glob.glob(r"C:\Program Files\gs\gs*\bin")
    gs_glob += glob.glob(r"C:\Program Files (x86)\gs\gs*\bin")
    if gs_glob:
        gs_dir = sorted(gs_glob)[-1]  # newest version
        found = _add_to_path_if_found("gswin64c.exe", [gs_dir], "Ghostscript")
        if not found:
            _add_to_path_if_found("gswin32c.exe", [gs_dir], "Ghostscript (32-bit)")
    else:
        print(
            "Info: Ghostscript not found — OCR will still work but PDF optimisation "
            "is limited. Install from https://ghostscript.com/releases/gsdnld.html",
            file=sys.stderr,
        )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="process_pdfs",
        description="Combine PDF files with optional OCR and Table of Contents.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python process_pdfs.py doc1.pdf,doc2.pdf
  python process_pdfs.py "*.pdf" -o book.pdf
  python process_pdfs.py scan.pdf --ocr --out searchable.pdf
  python process_pdfs.py "part*.pdf" --gen_toc -o book_with_toc.pdf
  python process_pdfs.py "vol*.pdf" --ocr --gen_toc -o complete.pdf
""",
    )
    parser.add_argument(
        "files",
        metavar="FILE_PATTERNS",
        help="Comma-separated list of PDF files or glob patterns",
    )
    parser.add_argument(
        "--out", "-o",
        default="combined.pdf",
        metavar="OUTPUT",
        help="Output filename (default: combined.pdf)",
    )
    parser.add_argument(
        "--ocr",
        action="store_true",
        help="OCR every input file before combining (requires Tesseract)",
    )
    parser.add_argument(
        "--gen_toc",
        action="store_true",
        help="Append a clickable Table of Contents page at the end",
    )
    return parser.parse_args()


# ---------------------------------------------------------------------------
# File pattern expansion
# ---------------------------------------------------------------------------

def expand_file_patterns(pattern_str: str) -> list[str]:
    """Expand a comma-separated string of glob patterns into an ordered, deduplicated list."""
    patterns = [p.strip() for p in pattern_str.split(",") if p.strip()]
    seen: set[str] = set()
    result: list[str] = []
    for pattern in patterns:
        matches = sorted(glob.glob(pattern, recursive=True))
        if not matches:
            print(f"Warning: no files matched '{pattern}'", file=sys.stderr)
        for match in matches:
            key = os.path.abspath(match)
            if key not in seen:
                seen.add(key)
                result.append(match)
    return result


# ---------------------------------------------------------------------------
# OCR
# ---------------------------------------------------------------------------

def ocr_file(src_path: str, dst_path: str) -> None:
    """Run ocrmypdf on src_path and write the result to dst_path.

    Pages that already contain text are skipped (--skip-text) to avoid
    degrading quality of already-searchable PDFs.
    """
    try:
        import ocrmypdf
    except ImportError:
        print("Error: ocrmypdf is not installed. Run: pip install ocrmypdf", file=sys.stderr)
        sys.exit(1)

    import io, contextlib
    stderr_buf = io.StringIO()
    try:
        with contextlib.redirect_stderr(stderr_buf):
            ocrmypdf.ocr(
                src_path,
                dst_path,
                skip_text=True,
                progress_bar=False,
            )
    except ocrmypdf.exceptions.PriorOcrFoundError:
        shutil.copy2(src_path, dst_path)
    except Exception as exc:
        print(f"Warning: OCR failed for '{src_path}': {exc}", file=sys.stderr)
        shutil.copy2(src_path, dst_path)
    finally:
        # Re-print captured stderr, filtering out [WinError 2] probe noise from
        # ocrmypdf checking for optional tools (Ghostscript, unpaper, pngquant …)
        for line in stderr_buf.getvalue().splitlines():
            if "[WinError 2]" not in line:
                print(line, file=sys.stderr)


# ---------------------------------------------------------------------------
# PDF combination
# ---------------------------------------------------------------------------

def combine_pdfs(
    pdf_paths: list[str],
    output_path: str,
    original_paths: list[str] | None = None,
    gen_toc: bool = False,
    extract_headings: bool = False,
) -> None:
    """Merge pdf_paths into output_path.

    Args:
        pdf_paths: Actual PDF files to read (may be OCR'd temp files).
        output_path: Destination for the combined PDF.
        original_paths: Parallel list of original filenames used for link resolution.
            Defaults to pdf_paths when not provided.
        gen_toc: When True, append a visible clickable TOC page at the end.
        extract_headings: When True, fall back to font-size analysis to derive
            headings from the text layer when a PDF has no existing bookmarks.
            Intended for OCR'd files.
    """
    import fitz
    import pikepdf

    if original_paths is None:
        original_paths = pdf_paths

    file_meta: list[tuple[str, int, int, list]] = []

    # ---- Step 1: merge with pikepdf ----
    # pikepdf.pages.extend() preserves ALL page annotations, including those
    # that PyMuPDF's insert_pdf() silently drops when source PDFs have broken
    # xref entries (a common defect in PDFs from older authoring tools).
    with pikepdf.Pdf.new() as merged:
        for pdf_path, orig_path in zip(pdf_paths, original_paths):
            page_offset = len(merged.pages)
            with pikepdf.open(pdf_path) as src:
                page_count = len(src.pages)
                merged.pages.extend(src.pages)
            toc = _get_toc(pdf_path)
            if extract_headings and not toc:
                toc = _extract_headings_from_text(pdf_path)
            file_meta.append((orig_path, page_offset, page_count, toc))

        # pikepdf.pages.extend() copies Widget annotation /Parent form-field
        # objects transitively but does NOT register them in /AcroForm/Fields.
        # Without that registration, PDF viewers treat the buttons as inert.
        _rebuild_acroform(merged)

        merged.save(output_path)

    # ---- Step 2: fix all external links (GoToR + Launch) via pikepdf ----
    _fix_external_links(output_path, file_meta)

    # ---- Step 3: add TOC page and bookmarks via PyMuPDF ----
    # garbage=0 avoids re-compacting the xref, preserving the pikepdf structure.
    doc = fitz.open(output_path)
    if gen_toc:
        toc_page_idx = _add_toc_page(doc, file_meta)
        _set_bookmarks(doc, file_meta, toc_page_idx)
    else:
        _set_bookmarks(doc, file_meta)
    tmp = output_path + ".fitz_tmp"
    doc.save(tmp, garbage=0, deflate=True, clean=False)
    doc.close()
    os.replace(tmp, output_path)


def _get_toc(pdf_path: str) -> list:
    """Extract the bookmark/outline tree from a PDF using fitz."""
    import fitz
    try:
        doc = fitz.open(pdf_path)
        toc = doc.get_toc()
        doc.close()
        return toc
    except Exception:
        return []


def _extract_headings_from_text(pdf_path: str) -> list:
    """Derive headings from the PDF text layer by analysing font-size distribution.

    Returns a list of [level, title, page_num] entries (1-based page numbers)
    compatible with fitz TOC format.  Falls back to [] on any error or when the
    document has no meaningful size variation (e.g. uniform body text only).

    Algorithm:
    - Collect every text span's font size, excluding the top/bottom 7 % of each
      page (running headers/footers).
    - Treat the most-frequent rounded size as the body baseline.
    - Spans >= 1.4× baseline  →  level 1 heading
    - Spans >= 1.15× baseline →  level 2 heading
    - Bold spans at body size  →  level 3 heading
    """
    import fitz
    from collections import Counter

    try:
        doc = fitz.open(pdf_path)
    except Exception:
        return []

    spans_data: list[tuple[int, float, bool, str]] = []  # (pg_idx, size, bold, text)

    for pg_idx, page in enumerate(doc):
        page_h = page.rect.height
        margin_top = page_h * 0.07
        margin_bot = page_h * 0.93
        for block in page.get_text("dict", flags=fitz.TEXT_PRESERVE_WHITESPACE).get("blocks", []):
            if block.get("type") != 0:
                continue
            for line in block.get("lines", []):
                y_top = line["bbox"][1]
                if y_top < margin_top or y_top > margin_bot:
                    continue
                parts, max_size, is_bold = [], 0.0, False
                for span in line.get("spans", []):
                    t = span.get("text", "").strip()
                    if t:
                        parts.append(t)
                        sz = float(span.get("size", 0))
                        if sz > max_size:
                            max_size = sz
                        if span.get("flags", 0) & 16:  # bold flag
                            is_bold = True
                text = " ".join(parts).strip()
                if text and max_size > 0:
                    spans_data.append((pg_idx, max_size, is_bold, text))

    doc.close()

    if not spans_data:
        return []

    # Body size = mode of all collected sizes (rounded to 1 decimal place)
    size_counts = Counter(round(s[1], 1) for s in spans_data)
    body_size = size_counts.most_common(1)[0][0]
    if body_size <= 0:
        return []

    total_pages = max(1, (max(s[0] for s in spans_data) + 1))

    raw: list = []
    for pg_idx, size, bold, text in spans_data:
        if len(text) < 3 or len(text) > 120:
            continue
        if text.replace(".", "").replace(" ", "").isdigit():
            continue  # skip pure page numbers / numbering

        ratio = size / body_size
        if ratio >= 1.4:
            level = 1
        elif ratio >= 1.15:
            level = 2
        elif bold and ratio >= 0.95:
            level = 3
        else:
            continue

        raw.append([level, text, pg_idx + 1])

    # Filter out repeating elements (navigation buttons, running headers).
    # Text appearing on more than 20 % of pages or more than 4 times is noise.
    text_freq = Counter(h[1] for h in raw)
    max_allowed = max(4, total_pages * 0.20)
    raw = [h for h in raw if text_freq[h[1]] <= max_allowed]

    # Remove consecutive duplicate (page, text) pairs produced by multi-span lines.
    headings: list = []
    seen_key: tuple | None = None
    for h in raw:
        key = (h[2], h[1])
        if key != seen_key:
            headings.append(h)
            seen_key = key

    return headings


def _rebuild_acroform(merged: "pikepdf.Pdf") -> None:
    """Register Widget annotations' root form fields in /AcroForm/Fields.

    pikepdf.pages.extend() copies Widget /Parent form-field objects transitively
    but does NOT add them to /AcroForm/Fields, so PDF viewers treat the buttons as
    inert. This function walks every Widget annotation, follows the /Parent chain to
    the root field, and adds it to the document's /AcroForm/Fields array.
    """
    import pikepdf

    root_fields: list = []
    seen: set = set()
    seen_names: dict = {}  # /T value → count of times seen

    for page in merged.pages:
        for annot in page.get("/Annots", pikepdf.Array()):
            try:
                if str(annot.get("/Subtype", "")) != "/Widget":
                    continue
                obj = annot
                parent = obj.get("/Parent")
                while parent is not None:
                    obj = parent
                    parent = obj.get("/Parent")
                try:
                    objgen = obj.objgen
                    if objgen not in seen:
                        seen.add(objgen)
                        # Rename duplicate /T field names so the viewer treats each
                        # merged file's buttons as independent fields.  If both
                        # source PDFs have a root field named '1', the second becomes
                        # '1_2', the third '1_3', etc.
                        t = str(obj.get("/T", ""))
                        if t in seen_names:
                            seen_names[t] += 1
                            obj["/T"] = pikepdf.String(t + "_" + str(seen_names[t]))
                        else:
                            seen_names[t] = 1
                        root_fields.append(obj)
                except AttributeError:
                    pass
            except Exception:
                continue

    if not root_fields:
        return

    if "/AcroForm" not in merged.Root:
        merged.Root["/AcroForm"] = pikepdf.Dictionary(
            Fields=pikepdf.Array(root_fields),
            DA=pikepdf.String("/Helv 0 Tf 0 g"),
        )
    else:
        existing = list(merged.Root["/AcroForm"].get("/Fields", pikepdf.Array()))
        existing_objgens = {o.objgen for o in existing if hasattr(o, "objgen")}
        new_fields = [
            f for f in root_fields
            if not hasattr(f, "objgen") or f.objgen not in existing_objgens
        ]
        merged.Root["/AcroForm"]["/Fields"] = pikepdf.Array(existing + new_fields)


# ---------------------------------------------------------------------------
# Cross-file link fixing
# ---------------------------------------------------------------------------

def _build_offset_lookup(file_meta: list) -> dict[str, int]:
    """Build a dict mapping every plausible path form → page offset in combined doc."""
    offset_by: dict[str, int] = {}
    for orig_path, page_offset, _, _ in file_meta:
        abs_path = os.path.abspath(orig_path)
        for key in (abs_path, orig_path, os.path.basename(orig_path), Path(orig_path).stem):
            offset_by[key] = page_offset
    return offset_by


def _resolve_offset(
    target_file: str,
    offset_by_path: dict[str, int],
) -> int | None:
    """Try multiple path representations to map a link target to a page offset."""
    candidates = [
        target_file,
        os.path.abspath(target_file),
        os.path.basename(target_file),
        Path(target_file).stem,
    ]
    for candidate in candidates:
        if candidate in offset_by_path:
            return offset_by_path[candidate]
    return None


def _fix_external_links(output_path: str, file_meta: list) -> None:
    """Rewrite /GoToR and /Launch links that target input PDFs as internal /GoTo.

    Both link types are handled in a single pikepdf pass since PyMuPDF's
    get_links() only surfaces /GoToR and silently drops /Launch annotations.
    """
    import pikepdf

    offset_by = _build_offset_lookup(file_meta)
    changed = False

    with pikepdf.open(output_path, allow_overwriting_input=True) as pdf:
        total_pages = len(pdf.pages)

        for page in pdf.pages:
            annots = page.get("/Annots", pikepdf.Array())
            if not annots:
                continue

            for annot in annots:
                try:
                    if str(annot.get("/Subtype", "")) not in ("/Link", "/Widget"):
                        continue
                    action = annot.get("/A")
                    if action is None:
                        continue
                    s = str(action.get("/S", ""))
                    if s not in ("/GoToR", "/Launch"):
                        continue

                    # Extract filename from /F (string or FileSpec dictionary)
                    f_obj = action.get("/F")
                    if f_obj is None:
                        continue
                    target = (
                        str(f_obj.get("/F", f_obj)) if hasattr(f_obj, "get") else str(f_obj)
                    )
                    if not target:
                        continue

                    offset = _resolve_offset(target, offset_by)
                    if offset is None:
                        continue  # not one of our input files; leave unchanged

                    # /GoToR carries an optional page-within-file in /D
                    page_in_file = 0
                    if s == "/GoToR":
                        d = action.get("/D")
                        if isinstance(d, pikepdf.Array) and len(d) > 0:
                            try:
                                page_in_file = max(0, int(d[0]))
                            except (TypeError, ValueError):
                                page_in_file = 0

                    abs_page_idx = max(0, min(offset + page_in_file, total_pages - 1))
                    annot["/A"] = pikepdf.Dictionary(
                        S=pikepdf.Name("/GoTo"),
                        D=pikepdf.Array([
                            pdf.pages[abs_page_idx].obj,
                            pikepdf.Name("/Fit"),
                        ]),
                    )
                    changed = True

                except Exception:
                    continue

        if changed:
            pdf.save()  # overwrite in place (allow_overwriting_input=True)


# ---------------------------------------------------------------------------
# PDF bookmark / outline
# ---------------------------------------------------------------------------

def _set_bookmarks(doc, file_meta: list, toc_page_idx: int | None = None) -> None:
    """Write a merged PDF outline (bookmark tree) for the combined document.

    Each input file gets a level-1 entry; its own bookmarks are indented one level.
    If toc_page_idx is given, a final "Table of Contents" entry is appended.
    """
    combined_toc: list[list] = []

    for orig_path, page_offset, _, toc in file_meta:
        title = Path(orig_path).stem
        # Level-1 entry for the file (PyMuPDF uses 1-based page numbers in TOC)
        combined_toc.append([1, title, page_offset + 1])
        for entry in toc:
            level, entry_title, page_num, *rest = entry
            # Shift level down by 1 (file entry is level 1)
            combined_toc.append([level + 1, entry_title, page_num + page_offset])

    if toc_page_idx is not None:
        combined_toc.append([1, "Table of Contents", toc_page_idx + 1])

    doc.set_toc(combined_toc)


# ---------------------------------------------------------------------------
# Visible TOC page — ReportLab layout + PyMuPDF link annotations
# ---------------------------------------------------------------------------

_TOC_PAGE_W   = 595.28   # A4 width  in points
_TOC_PAGE_H   = 841.89   # A4 height in points
_TOC_MARGIN   = 50.0
_TOC_LINE_H   = 18.0
_TOC_HEADER_H = 65.0
_TOC_SEC_GAP  = 12.0
_TOC_MIN_Y    = 45.0     # min ReportLab y before forcing a new page


def _render_toc_reportlab(file_meta: list, doc_page_count: int) -> tuple:
    """Render TOC pages with ReportLab; return (pdf_bytes, link_rows).

    link_rows — list of dicts:
        toc_page   : 0-based page index in the returned PDF
        rl_y_top   : top    of the clickable row, ReportLab coords (y from bottom)
        rl_y_bot   : bottom of the clickable row, ReportLab coords
        x0, x1     : horizontal extent of the clickable area (points)
        target_page: 0-based page index in the combined document
    """
    from reportlab.pdfgen import canvas as rl_canvas
    from reportlab.pdfbase.pdfmetrics import stringWidth
    import io

    PW, PH  = _TOC_PAGE_W, _TOC_PAGE_H
    M       = _TOC_MARGIN
    LINE_H  = _TOC_LINE_H
    HDR_H   = _TOC_HEADER_H
    SEC_GAP = _TOC_SEC_GAP
    MIN_Y   = _TOC_MIN_Y

    C_HDR_BG  = (0.15, 0.25, 0.50)
    C_HDR_TXT = (1.00, 1.00, 1.00)
    C_FILE    = (0.10, 0.10, 0.50)
    C_ENTRY   = (0.00, 0.00, 0.60)
    C_PAGENUM = (0.40, 0.40, 0.40)
    C_DOT     = (0.70, 0.70, 0.70)

    buf = io.BytesIO()
    c = rl_canvas.Canvas(buf, pagesize=(PW, PH))
    link_rows: list = []
    toc_page = 0

    def draw_header() -> None:
        c.setFillColorRGB(*C_HDR_BG)
        c.rect(0, PH - HDR_H, PW, HDR_H, fill=1, stroke=0)
        c.setFillColorRGB(*C_HDR_TXT)
        c.setFont("Helvetica-Bold", 20)
        c.drawString(M, PH - HDR_H + 18, "Table of Contents")

    def new_page() -> float:
        nonlocal toc_page
        c.showPage()
        toc_page += 1
        draw_header()
        return PH - HDR_H - 22.0

    def ensure(rl_y: float, need: float) -> float:
        return new_page() if rl_y - need < MIN_Y else rl_y

    def record_link(rl_y: float, fs: float, x0: float, x1: float,
                    target: int) -> None:
        link_rows.append({
            "toc_page":    toc_page,
            "rl_y_top":    rl_y + fs * 0.85,
            "rl_y_bot":    rl_y - fs * 0.30,
            "x0":          x0,
            "x1":          x1,
            "target_page": target,
        })

    def draw_entry(rl_y: float, indent: float, title: str,
                   font_name: str, fs: float,
                   target: int, display_page: int) -> None:
        pn_text = f"p. {display_page}"
        pn_w    = stringWidth(pn_text, "Helvetica", fs)
        pn_x    = PW - M - pn_w

        # Truncate title so it never overlaps the page-number column
        max_w = pn_x - indent - 14.0
        while len(title) > 4 and stringWidth(title, font_name, fs) > max_w:
            title = title[:-1]

        title_end_x = indent + stringWidth(title, font_name, fs) + 4.0

        # Dot leaders
        dot_w = stringWidth(".", "Helvetica", fs - 1)
        x = title_end_x + 3.0
        c.setFillColorRGB(*C_DOT)
        c.setFont("Helvetica", fs - 1)
        while x + dot_w + 2 < pn_x - 4:
            c.drawString(x, rl_y - 1, ".")
            x += dot_w + 3.0

        c.setFont(font_name, fs)
        c.setFillColorRGB(*C_ENTRY)
        c.drawString(indent, rl_y, title)

        c.setFont("Helvetica", fs)
        c.setFillColorRGB(*C_PAGENUM)
        c.drawRightString(PW - M, rl_y, pn_text)

        record_link(rl_y, fs, indent, PW - M, target)

    # ---- first page ----
    draw_header()
    rl_y: float = PH - HDR_H - 22.0

    for orig_path, page_offset, _count, toc in file_meta:
        rl_y = ensure(rl_y, LINE_H + 4)

        file_title = Path(orig_path).stem
        c.setFont("Helvetica-Bold", 13)
        c.setFillColorRGB(*C_FILE)
        c.drawString(M, rl_y, file_title)
        record_link(rl_y, 13.0, M, PW - M, page_offset)
        rl_y -= LINE_H + 4

        entries = toc if toc else []
        if not entries:
            rl_y = ensure(rl_y, LINE_H)
            c.setFont("Helvetica-Oblique", 10)
            c.setFillColorRGB(*C_PAGENUM)
            c.drawString(M + 16, rl_y, "(start of document)")
            rl_y -= LINE_H
        else:
            for entry in entries:
                level, entry_title, entry_page_num, *_ = entry
                abs_page_0 = min(
                    page_offset + max(0, entry_page_num - 1),
                    doc_page_count - 1,
                )
                rl_y = ensure(rl_y, LINE_H)
                indent = M + (level - 1) * 16.0
                fn = "Helvetica-Bold" if level == 1 else "Helvetica"
                fs = 11.0 if level == 1 else 10.0
                draw_entry(rl_y, indent, entry_title, fn, fs,
                           abs_page_0, abs_page_0 + 1)
                rl_y -= LINE_H

        rl_y -= SEC_GAP

    c.save()
    return buf.getvalue(), link_rows


def _add_toc_page(doc, file_meta: list) -> int:
    """Append TOC page(s) and return the 0-based index of the first TOC page.

    ReportLab renders the visual layout and returns exact row bounds.
    PyMuPDF inserts those pages then stamps link annotations at the bounds.
    """
    import fitz

    toc_bytes, link_rows = _render_toc_reportlab(file_meta, len(doc))

    toc_doc   = fitz.open("pdf", toc_bytes)
    insert_at = len(doc)
    doc.insert_pdf(toc_doc)
    toc_doc.close()

    for row in link_rows:
        pg = doc[insert_at + row["toc_page"]]
        ph = pg.rect.height          # actual height from the inserted PDF
        # ReportLab y is from the bottom; fitz y is from the top.
        fitz_y_top = ph - row["rl_y_top"]
        fitz_y_bot = ph - row["rl_y_bot"]
        pg.insert_link({
            "kind": fitz.LINK_GOTO,
            "from": fitz.Rect(row["x0"], fitz_y_top, row["x1"], fitz_y_bot),
            "page": row["target_page"],
            "to":   fitz.Point(0, 0),
        })

    return insert_at


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    args = parse_args()

    # --- Resolve input file list ---
    input_files = expand_file_patterns(args.files)
    if not input_files:
        print("Error: no input files found.", file=sys.stderr)
        sys.exit(1)

    print(f"Input ({len(input_files)} file{'s' if len(input_files) != 1 else ''}):")
    for f in input_files:
        print(f"  {f}")

    pdf_paths = list(input_files)
    original_paths = list(input_files)
    tmpdir: str | None = None

    # --- OCR pass (optional) ---
    if args.ocr:
        _ensure_tesseract_on_path()
        _ensure_ghostscript_on_path()
        print("\nRunning OCR ...")
        tmpdir = tempfile.mkdtemp(prefix="process_pdfs_ocr_")
        ocr_paths: list[str] = []
        for i, fp in enumerate(input_files):
            dst = os.path.join(tmpdir, f"ocr_{i:04d}_{os.path.basename(fp)}")
            print(f"  [{i + 1}/{len(input_files)}] {fp}")
            ocr_file(fp, dst)
            ocr_paths.append(dst)
        pdf_paths = ocr_paths

    # --- Combine ---
    try:
        print(f"\nCombining into '{args.out}' ...")
        combine_pdfs(
            pdf_paths,
            args.out,
            original_paths=original_paths,
            gen_toc=args.gen_toc,
            extract_headings=args.ocr,
        )
    finally:
        if tmpdir:
            shutil.rmtree(tmpdir, ignore_errors=True)

    abs_out = os.path.abspath(args.out)
    size_mb = os.path.getsize(abs_out) / 1_048_576
    print(f"\nDone.  Output: {abs_out}  ({size_mb:.1f} MB)")


if __name__ == "__main__":
    main()
