# Master Prompt

This document records the original requirements for `process_pdfs.py`.

## Original Request

> Create a python script for combining and OCR-ing PDF files. Call the python script process_pdfs.py.
> The list of files that will be combined can be a comma separated list. Allow wildcards in the filenames.
> The --out (or --o) command line option allows the user to specify the name of the output file.
> The default name for the output file is "combined.pdf".
> The --ocr command line option instructs the script to use the ocrmypdf package to add perform OCR of all
> the input PDF files and supplement the PDF with searchable text in an invisible layer to the original file(s).
> If the --gen_toc option is present, then generate a Table of Content (ToC) for all the input files chapters
> and append at the end of the combined files. If possible generate clickable links for the page numbers in that ToC.
> If PDF files have links (or references) to other input files, please expand those links so they will work
> correctly in combined file.
> Use github as the repository and check in intermediary steps.
> Create and updated MASTER_PROMPT.md, README.md and CLAUDE.md for each check-in.

## Requirements Breakdown

| # | Requirement | Status |
|---|-------------|--------|
| 1 | Accept comma-separated list of PDF files with wildcard/glob support | ✅ Done |
| 2 | `--out` / `-o` flag sets output filename (default: `combined.pdf`) | ✅ Done |
| 3 | `--ocr` flag runs `ocrmypdf` to add invisible searchable text layer | ✅ Done |
| 4 | `--gen_toc` flag appends a Table of Contents page with clickable links | ✅ Done |
| 5 | Cross-file PDF links (GoToR) rewritten as internal GoTo links | ✅ Done |
| 6 | Incremental GitHub commits with updated docs at each step | ✅ Done |

## Verification

```bash
# Combine two PDFs
python process_pdfs.py 86etm.pdf,86etm2.pdf -o combined.pdf

# Combine all with TOC
python process_pdfs.py "86etm*.pdf" --gen_toc -o combined_toc.pdf

# OCR (requires Tesseract on PATH)
python process_pdfs.py 86etm.pdf --ocr -o combined_ocr.pdf

# All options
python process_pdfs.py "86etm*.pdf" --ocr --gen_toc -o complete.pdf
```

## Implementation Notes

- Primary PDF library: **PyMuPDF** (`fitz`) — handles merging, bookmarks, links, annotations
- OCR pipeline: **ocrmypdf** — wraps Tesseract; `--skip-text` prevents re-OCR of existing text
- Cross-file link resolution: GoToR links matched by absolute path, relative path, and basename
- TOC page is appended at the **end** of the combined PDF (per spec) and also written to the
  PDF outline/bookmark tree for sidebar navigation
