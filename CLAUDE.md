# process_pdfs — Project Context for Claude

## What This Project Does

`process_pdfs.py` combines multiple PDF files into one, with optional OCR and Table of Contents generation.

## Key Files

| File | Purpose |
|------|---------|
| `process_pdfs.py` | Main CLI script — all logic lives here |
| `requirements.txt` | `PyMuPDF`, `ocrmypdf`, `pypdf` |
| `README.md` | User-facing usage docs |
| `MASTER_PROMPT.md` | Original requirements and implementation status |
| `CLAUDE.md` | This file — architecture notes for Claude |

## Architecture

```
main()
 ├── parse_args()                 argparse: files, --out/-o, --ocr, --gen_toc
 ├── expand_file_patterns()       split on comma, glob each pattern, deduplicate
 ├── ocr_file(src, dst)           ocrmypdf.ocr(..., skip_text=True)
 └── combine_pdfs(pdfs, out, ...)
      ├── pikepdf.Pdf.new() + pages.extend()   merge pages — preserves ALL annotations
      ├── _fix_external_links()                 /GoToR + /Launch → /GoTo (pikepdf pass)
      ├── fitz.open()                           re-open for TOC/bookmark decoration
      ├── _add_toc_page()                       append visible TOC page with link annotations
      ├── _set_bookmarks()                      doc.set_toc() with merged outline
      └── fitz.save(garbage=0)                  save without xref re-compaction
```

### Why pikepdf for the Merge

PyMuPDF's `insert_pdf()` silently drops annotations that contain indirect references to
objects with broken xref entries — a common defect in PDFs from older authoring tools.
For `86etm.pdf`, this caused 34 of 62 annotations to be lost (PyMuPDF kept 28; pikepdf
keeps all 62). pikepdf's `pages.extend()` is more lenient: it copies page annotations
as-is without trying to resolve every referenced object.

### Why pikepdf for Link Fixing

PyMuPDF's `get_links()` surfaces `/GoToR` but **silently drops `/Launch` annotations**.
`_fix_external_links()` handles both in a single pikepdf pass over the raw annotation
dictionaries, replacing either action type with `/GoTo` + page-object reference when the
target file is one of the inputs.

### Why `garbage=0` on the Final fitz Save

`garbage=4` re-compacts the xref and can drop objects that have dangling references. With
`garbage=0` PyMuPDF only appends new objects (TOC page, bookmark tree) without touching
existing ones.

## Libraries

| Library | Role |
|---------|------|
| `PyMuPDF` (`fitz`) | PDF I/O, merging, GoToR links, annotations, bookmarks |
| `pikepdf` | Low-level PDF object access — used to fix /Launch annotations |
| `ocrmypdf` | OCR pipeline wrapper around Tesseract |
| `pypdf` | Installed as fallback, not actively used |

## Commit History Intent

Each git commit represents a working, testable milestone:

1. **init** — project scaffolding (docs, requirements, .gitignore)
2. **core** — basic PDF combining with CLI, cross-file link fixing
3. **ocr** — OCR integration via ocrmypdf
4. **toc** — Table of Contents page with clickable links

## Important Implementation Details

- Page offsets use 0-based indexing everywhere internally; PyMuPDF `set_toc()` and `get_toc()` use **1-based** page numbers — adjust accordingly
- `_fix_cross_file_links()` must run before `_add_toc_page()` so TOC link targets are stable
- OCR files are written to a `tempfile.mkdtemp()` directory and cleaned up in a `finally` block
- The `original_paths` parameter in `combine_pdfs()` lets the link-fixer use the real filenames even when reading OCR'd temp files
- The TOC page is appended at the **end** (per user spec), which is unusual but intentional

## Testing

```bash
# Quick smoke test — combine two PDFs
python process_pdfs.py 86etm.pdf,86etm2.pdf -o test_out.pdf

# With TOC
python process_pdfs.py "86etm*.pdf" --gen_toc -o toc_out.pdf

# With OCR (needs Tesseract installed)
python process_pdfs.py 86etm.pdf --ocr -o ocr_out.pdf

# All options
python process_pdfs.py "86etm*.pdf" --ocr --gen_toc -o complete.pdf
```

## Sample Files

The repository root contains sample PDFs (`86etm*.pdf`, `86howto.pdf`) that were present
when the project was initialized — use these for smoke-testing.

## Edge Cases Handled

| Situation | Behaviour |
|-----------|-----------|
| Pattern matches no files | Warning printed; script aborts if *all* patterns fail |
| Tesseract not on PATH | Warning + original file copied; combination continues |
| Input PDF already has OCR text | `skip_text=True` skips re-OCR for that page |
| GoToR link target not in input list | Link left as-is (GoToR) |
| Overflowing TOC | New page added automatically |
