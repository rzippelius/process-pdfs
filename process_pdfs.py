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
    _ensure_tesseract_on_path()
    _ensure_ghostscript_on_path()

    try:
        import ocrmypdf
    except ImportError:
        print("Error: ocrmypdf is not installed. Run: pip install ocrmypdf", file=sys.stderr)
        sys.exit(1)

    try:
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


# ---------------------------------------------------------------------------
# PDF combination
# ---------------------------------------------------------------------------

def combine_pdfs(
    pdf_paths: list[str],
    output_path: str,
    original_paths: list[str] | None = None,
    gen_toc: bool = False,
) -> None:
    """Merge pdf_paths into output_path.

    Args:
        pdf_paths: Actual PDF files to read (may be OCR'd temp files).
        output_path: Destination for the combined PDF.
        original_paths: Parallel list of original filenames used for link resolution.
            Defaults to pdf_paths when not provided.
        gen_toc: When True, append a visible clickable TOC page at the end.
    """
    import fitz  # PyMuPDF

    if original_paths is None:
        original_paths = pdf_paths

    combined = fitz.open()
    # file_meta: list of (original_path, page_offset, page_count, toc)
    file_meta: list[tuple[str, int, int, list]] = []

    for pdf_path, orig_path in zip(pdf_paths, original_paths):
        page_offset = len(combined)
        src = fitz.open(pdf_path)
        toc = src.get_toc()
        page_count = len(src)
        combined.insert_pdf(src)
        src.close()
        file_meta.append((orig_path, page_offset, page_count, toc))

    _fix_cross_file_links(combined, file_meta)

    if gen_toc:
        _add_toc_page(combined, file_meta)
        # Add a bookmark pointing to the TOC page itself
        toc_page_idx = len(combined) - 1  # last page
        _set_bookmarks(combined, file_meta, toc_page_idx)
    else:
        _set_bookmarks(combined, file_meta)

    combined.save(output_path, garbage=4, deflate=True)
    combined.close()

    # Second pass: fix /Launch annotations that PyMuPDF's get_links() does not expose.
    # These are common when PDFs use /Launch to open companion PDF files.
    _fix_launch_links(output_path, file_meta)


# ---------------------------------------------------------------------------
# Cross-file link fixing — shared helpers
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


def _fix_cross_file_links(doc, file_meta: list) -> None:
    """Convert GoToR (external-file) links into internal GoTo links via PyMuPDF.

    PyMuPDF's get_links() exposes GoToR links but not /Launch annotations;
    the latter are handled by _fix_launch_links() after saving.
    """
    import fitz

    offset_by_path = _build_offset_lookup(file_meta)
    total_pages = len(doc)

    for page_num in range(total_pages):
        page = doc[page_num]
        links_to_fix = [lk for lk in page.get_links() if lk.get("kind") == fitz.LINK_GOTOR]

        for link in links_to_fix:
            target_file = link.get("file", "")
            if not target_file:
                continue
            offset = _resolve_offset(target_file, offset_by_path)
            if offset is None:
                continue

            # link["page"] is 0-indexed within the referenced file
            target_page = max(0, min(link.get("page", 0) + offset, total_pages - 1))

            page.delete_link(link)
            page.insert_link({
                "kind": fitz.LINK_GOTO,
                "from": link["from"],
                "page": target_page,
                "to": fitz.Point(0, 0),
            })


def _fix_launch_links(output_path: str, file_meta: list) -> None:
    """Post-process the saved PDF with pikepdf to convert /Launch → /GoTo.

    PDF viewers open /Launch annotations by launching an external application;
    when the target is another input PDF, the correct behaviour after combining
    is to navigate to the first page of that file within the combined document.

    PyMuPDF's get_links() does not surface /Launch annotations, so this step
    uses pikepdf for direct PDF-object access.
    """
    import pikepdf

    offset_by = _build_offset_lookup(file_meta)
    changed = False

    with pikepdf.open(output_path, allow_overwriting_input=True) as pdf:
        total_pages = len(pdf.pages)

        for page in pdf.pages:
            raw_annots = page.get("/Annots", pikepdf.Array())
            if not raw_annots:
                continue

            for annot in raw_annots:
                try:
                    if str(annot.get("/Subtype", "")) != "/Link":
                        continue
                    action = annot.get("/A")
                    if action is None:
                        continue
                    if str(action.get("/S", "")) != "/Launch":
                        continue

                    # /F is either a string or a FileSpec dictionary
                    f_obj = action.get("/F")
                    if f_obj is None:
                        continue
                    target = (
                        str(f_obj.get("/F", f_obj))
                        if hasattr(f_obj, "get")
                        else str(f_obj)
                    )
                    if not target:
                        continue

                    offset = _resolve_offset(target, offset_by)
                    if offset is None:
                        continue  # target is not one of our input files

                    abs_page_idx = max(0, min(offset, total_pages - 1))
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
# Visible TOC page
# ---------------------------------------------------------------------------

_TOC_FONT = "helv"
_TOC_MARGIN = 50
_TOC_LINE_H = 18
_COL_DOT = 0.55  # Fraction of page width where dot leaders start
_COLOR_HEADING = (0.10, 0.10, 0.50)
_COLOR_LINK = (0.00, 0.00, 0.75)
_COLOR_PAGENUM = (0.25, 0.25, 0.25)
_COLOR_BLACK = (0.0, 0.0, 0.0)


def _add_toc_page(doc, file_meta: list) -> None:
    """Append one or more styled, clickable Table of Contents pages to doc."""
    import fitz

    def _new_toc_page() -> tuple:
        """Add a blank page and draw the header; return (page, current_y)."""
        pg = doc.new_page()
        pw = pg.rect.width
        # Title bar
        pg.draw_rect(
            fitz.Rect(0, 0, pw, _TOC_MARGIN + 35),
            color=None,
            fill=(0.15, 0.25, 0.50),
        )
        pg.insert_text(
            fitz.Point(_TOC_MARGIN, _TOC_MARGIN + 22),
            "Table of Contents",
            fontsize=20,
            fontname=_TOC_FONT,
            color=(1.0, 1.0, 1.0),
        )
        return pg, _TOC_MARGIN + 55

    def _ensure_space(pg, y: float) -> tuple:
        """Start a new page if too little space remains."""
        if y > pg.rect.height - _TOC_MARGIN:
            pg, y = _new_toc_page()
        return pg, y

    page, y = _new_toc_page()
    pw = page.rect.width
    dot_x = pw * _COL_DOT

    for orig_path, page_offset, page_count, toc in file_meta:
        page, y = _ensure_space(page, y)
        pw = page.rect.width
        dot_x = pw * _COL_DOT

        file_title = Path(orig_path).stem

        # ---- File-level heading ----
        page.insert_text(
            fitz.Point(_TOC_MARGIN, y),
            file_title,
            fontsize=13,
            fontname=_TOC_FONT,
            color=_COLOR_HEADING,
        )
        # Clickable rect for the file heading
        heading_rect = fitz.Rect(_TOC_MARGIN, y - 12, pw - _TOC_MARGIN, y + 4)
        page.insert_link({
            "kind": fitz.LINK_GOTO,
            "from": heading_rect,
            "page": page_offset,
            "to": fitz.Point(0, 0),
        })
        y += _TOC_LINE_H + 4

        # ---- Chapter entries from the file's own bookmark tree ----
        entries = toc if toc else []
        if not entries:
            # File has no bookmarks — show a generic "Start of document" entry
            page, y = _ensure_space(page, y)
            _draw_toc_entry(
                page, y,
                indent=_TOC_MARGIN + 16,
                title="(start of document)",
                page_num_display=page_offset + 1,
                target_page=page_offset,
                dot_x=dot_x,
                pw=pw,
                fontsize=10,
                color=_COLOR_PAGENUM,
            )
            y += _TOC_LINE_H
        else:
            for entry in entries:
                level, entry_title, entry_page_num, *_ = entry
                # entry_page_num is 1-based within the source file
                abs_page_0 = max(0, min(entry_page_num - 1 + page_offset, len(doc) - 1))
                display_page = abs_page_0 + 1

                page, y = _ensure_space(page, y)
                pw = page.rect.width
                dot_x = pw * _COL_DOT

                indent = _TOC_MARGIN + (level - 1) * 16
                _draw_toc_entry(
                    page, y,
                    indent=indent,
                    title=entry_title,
                    page_num_display=display_page,
                    target_page=abs_page_0,
                    dot_x=dot_x,
                    pw=pw,
                    fontsize=10 if level > 1 else 11,
                    color=_COLOR_LINK,
                )
                y += _TOC_LINE_H

        y += 12  # Gap between file sections


def _draw_toc_entry(
    page,
    y: float,
    *,
    indent: float,
    title: str,
    page_num_display: int,
    target_page: int,
    dot_x: float,
    pw: float,
    fontsize: float,
    color: tuple,
) -> None:
    """Draw one TOC line: title ... page_num, with a clickable link over the whole line."""
    import fitz

    margin = _TOC_MARGIN
    # Estimate max chars before dot column (6 pts per char is a rough approximation)
    max_chars = max(10, int((dot_x - indent) / (fontsize * 0.55)))
    if len(title) > max_chars:
        title = title[: max_chars - 1] + "…"  # ellipsis

    page.insert_text(fitz.Point(indent, y), title, fontsize=fontsize, fontname=_TOC_FONT, color=color)

    page_label = f"p. {page_num_display}"  # narrow non-breaking space
    page.insert_text(
        fitz.Point(dot_x, y), page_label, fontsize=fontsize, fontname=_TOC_FONT, color=_COLOR_PAGENUM
    )

    # Clickable annotation covering the entire row
    link_rect = fitz.Rect(indent, y - fontsize, pw - margin, y + 3)
    page.insert_link({
        "kind": fitz.LINK_GOTO,
        "from": link_rect,
        "page": target_page,
        "to": fitz.Point(0, 0),
    })


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
        )
    finally:
        if tmpdir:
            shutil.rmtree(tmpdir, ignore_errors=True)

    abs_out = os.path.abspath(args.out)
    size_mb = os.path.getsize(abs_out) / 1_048_576
    print(f"\nDone.  Output: {abs_out}  ({size_mb:.1f} MB)")


if __name__ == "__main__":
    main()
