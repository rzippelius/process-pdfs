# process_pdfs

A command-line tool for combining, OCR-ing, and organizing PDF files into a single searchable document.

## Features

- Combine any number of PDFs in order
- Wildcard / glob support in file patterns (`*.pdf`, `chapter?.pdf`, etc.)
- Optional OCR via `ocrmypdf` — adds an invisible searchable text layer without altering appearance
- Optional Table of Contents page appended at the end with clickable page links
- PDF outline / bookmarks merged and preserved across all input files
- Cross-file links automatically rewritten so they work inside the combined PDF

## Requirements

### Python

Python 3.9 or later.

```bash
pip install -r requirements.txt
```

### External: Tesseract OCR (only needed for `--ocr`)

| Platform | Install |
|----------|---------|
| Windows  | [UB-Mannheim installer](https://github.com/UB-Mannheim/tesseract/wiki) |
| macOS    | `brew install tesseract` |
| Linux    | `sudo apt install tesseract-ocr` |

Make sure `tesseract` is on your `PATH` before using `--ocr`.

## Usage

```
python process_pdfs.py FILE_PATTERNS [options]
```

`FILE_PATTERNS` is a **comma-separated** list of file paths or glob patterns.

### Options

| Option | Description |
|--------|-------------|
| `--out FILE` / `-o FILE` | Output filename (default: `combined.pdf`) |
| `--ocr` | OCR all input files before combining |
| `--gen_toc` | Append a clickable Table of Contents at the end |

### Examples

```bash
# Combine two files
python process_pdfs.py doc1.pdf,doc2.pdf

# Combine all PDFs in directory, custom output name
python process_pdfs.py "*.pdf" -o book.pdf

# Combine with OCR
python process_pdfs.py scan1.pdf,scan2.pdf --ocr --out searchable.pdf

# Combine with Table of Contents
python process_pdfs.py "part*.pdf" --gen_toc --out book_with_toc.pdf

# All options
python process_pdfs.py "vol*.pdf" --ocr --gen_toc -o complete.pdf
```

## How It Works

```
Input files
    │
    ├─ (--ocr) ocrmypdf --skip-text  →  OCR'd temp files
    │
    ▼
PyMuPDF insert_pdf()  →  combined document
    │
    ├─ Fix cross-file GoToR links → internal GoTo links
    │
    ├─ (--gen_toc) Append TOC page with clickable links
    │
    ├─ Write PDF outline / bookmark tree
    │
    └─ Save output PDF
```

### Cross-File Link Fixing

When PDFs reference each other (e.g., a linked index), those GoToR links are automatically
converted to internal GoTo links pointing to the correct page in the combined file.

Resolution order:
1. Absolute path match
2. Relative path match (from CWD)
3. Basename match (filename only)

### Table of Contents

The `--gen_toc` flag:
1. Reads the bookmark/outline tree from each input file
2. Appends a styled TOC page at the end of the combined PDF
3. Every entry is a clickable link to the correct page
4. Also writes the merged outline to the PDF's bookmark tree (sidebar navigation)

## Project Files

| File | Description |
|------|-------------|
| `process_pdfs.py` | Main script |
| `requirements.txt` | Python dependencies |
| `README.md` | This file |
| `MASTER_PROMPT.md` | Original requirements |
| `CLAUDE.md` | Project context for Claude |
