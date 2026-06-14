# AGENTS.md

## Project

Two-file tool for extracting book text from `elib.biblioatom.ru` via its `/rpc/bookviewer/cp/` endpoint.

- `biblioatom-extractor.user.js` — Tampermonkey userscript (UI panel + page fetcher). No build step; paste into Tampermonkey directly.
- `convert_book.py` — Python 3 CLI that converts the userscript's Raw JSON export into chapter-based HTML / FB2 / EPUB.

## Running

```bash
# Convert JSON export to all formats (HTML, FB2, EPUB)
python convert_book.py -i input.json -o output_books

# Specific formats only
python convert_book.py -i input.json -f html,epub

# Chapter detection modes: strict (default) or normal
python convert_book.py -i input.json --chapter-mode normal
```

No dependencies beyond Python 3 stdlib. No tests, no linting configured.

## Conventions

- Userscript UI text is in Russian (buttons, labels, log messages).
- `convert_book.py` is also Russian-oriented (front matter titles, heading detection heuristics).
- No package manager, no `package.json`, no `requirements.txt`.
