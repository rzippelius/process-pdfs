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
      ├── fitz.open() + insert_pdf() per file   build combined doc, collect file_meta
      ├── _fix_cross_file_links()               GoToR → GoTo with page offset
      ├── _add_toc_page()                       append visible TOC page with link annotations
      └── _set_bookmarks()                      doc.set_toc() with merged outline
```

## Libraries

| Library | Role |
|---------|------|
| `PyMuPDF` (`fitz`) | All PDF I/O, merging, links, annotations, bookmarks |
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
python process_pdfs.py a.pdf,b.pdf -o test_out.pdf

# With OCR (needs Tesseract installed)
python process_pdfs.py scan.pdf --ocr -o ocr_out.pdf

# With TOC
python process_pdfs.py "*.pdf" --gen_toc -o toc_out.pdf
```
