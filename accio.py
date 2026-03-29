#!/usr/bin/env python3
"""
accio.py — PDF folder watcher that auto-renames academic papers.

Usage:
    python accio.py --input ~/papers --output ~/renamed
    python accio.py  # uses defaults: ~/papers -> ~/papers_renamed

Metadata resolution order:
  1. arXiv ID (from filename or first-page text)
  2. Semantic Scholar API (by title search)
  3. PDF embedded metadata
  4. Best-effort text extraction fallback

Output format: Author - Year - Title.pdf
"""

import re
import time
import logging
import argparse
import subprocess
import unicodedata
from pathlib import Path

import requests
from pypdf import PdfReader
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("accio")


# ── Notifications ─────────────────────────────────────────────────────────────

def notify(dest: Path) -> None:
    """Show a macOS notification via terminal-notifier with Open button."""
    subprocess.run(
        [
            "/opt/homebrew/bin/terminal-notifier",
            "-title", "Accio",
            "-message", dest.name,
            "-execute", f'open "{dest}"',
        ],
        check=False,
        capture_output=True,
    )


def notify_error(pdf_path: Path, error: Exception) -> None:
    """Show a macOS notification for processing failures."""
    subprocess.run(
        [
            "/opt/homebrew/bin/terminal-notifier",
            "-title", "Accio ⚠️",
            "-message", f"Failed: {pdf_path.name}",
            "-subtitle", str(error)[:80],
        ],
        check=False,
        capture_output=True,
    )


def notify_duplicate(pdf_path: Path, dest: Path) -> None:
    """Show a macOS notification for duplicate files."""
    subprocess.run(
        [
            "/opt/homebrew/bin/terminal-notifier",
            "-title", "Accio",
            "-message", f"Already exists: {dest.name}",
            "-subtitle", pdf_path.name,
        ],
        check=False,
        capture_output=True,
    )


# ── Helpers ───────────────────────────────────────────────────────────────────

def slugify(text: str, max_len: int = 80) -> str:
    text = unicodedata.normalize("NFKD", text)
    text = text.encode("ascii", "ignore").decode("ascii")
    text = re.sub(r'[^\w\s\-]', '', text)
    text = re.sub(r'\s+', ' ', text).strip()
    return text[:max_len]


def safe_filename(author: str, year: str, title: str) -> str:
    return f"{slugify(author)} - {year} - {slugify(title)}.pdf"


# ── Metadata extractors ───────────────────────────────────────────────────────

ARXIV_RE = re.compile(r'(\d{4}\.\d{4,5})(v\d+)?')


def extract_arxiv_id(pdf_path: Path) -> str | None:
    # 1. Filename
    m = ARXIV_RE.search(pdf_path.stem)
    if m:
        return m.group(1)
    # 2. First-page text
    try:
        reader = PdfReader(str(pdf_path))
        text = reader.pages[0].extract_text() or ""
        m = ARXIV_RE.search(text)
        if m:
            return m.group(1)
    except Exception:
        pass
    return None


def fetch_arxiv(arxiv_id: str) -> dict | None:
    url = f"https://export.arxiv.org/abs/{arxiv_id}"
    try:
        r = requests.get(url, timeout=10)
        r.raise_for_status()
        text = r.text
        title_m = re.search(r'<title>(.*?)</title>', text, re.DOTALL)
        raw_title = title_m.group(1) if title_m else ""
        raw_title = re.sub(r'\[.*?\]', '', raw_title).strip()
        raw_title = re.sub(r'\|.*', '', raw_title).strip()
        author_m = re.search(r'citation_author.*?content="([^"]+)"', text)
        author = author_m.group(1).split(",")[0].strip() if author_m else ""
        year_m = re.search(r'citation_date.*?content="(\d{4})', text)
        year = year_m.group(1) if year_m else ""
        if raw_title and author and year:
            return {"title": raw_title, "author": author, "year": year}
    except Exception as e:
        log.debug(f"arXiv fetch failed: {e}")
    return None


def fetch_semantic_scholar(title: str) -> dict | None:
    url = "https://api.semanticscholar.org/graph/v1/paper/search"
    params = {"query": title, "limit": 1, "fields": "title,authors,year"}
    try:
        r = requests.get(url, params=params, timeout=10)
        r.raise_for_status()
        papers = r.json().get("data", [])
        if not papers:
            return None
        p = papers[0]
        authors = p.get("authors", [])
        first_author = authors[0]["name"].split()[-1] if authors else ""
        return {
            "title": p.get("title", ""),
            "author": first_author,
            "year": str(p.get("year", "")),
        }
    except Exception as e:
        log.debug(f"Semantic Scholar fetch failed: {e}")
    return None


def extract_pdf_metadata(pdf_path: Path) -> dict | None:
    try:
        reader = PdfReader(str(pdf_path))
        meta = reader.metadata or {}
        title = (meta.get("/Title") or "").strip()
        author = (meta.get("/Author") or "").strip()
        year = ""
        date_str = meta.get("/CreationDate") or meta.get("/ModDate") or ""
        ym = re.search(r'(\d{4})', date_str)
        if ym:
            year = ym.group(1)
        if title and author:
            return {"title": title, "author": author.split(";")[0].split(",")[0], "year": year}
    except Exception as e:
        log.debug(f"PDF metadata extraction failed: {e}")
    return None


def extract_text_fallback(pdf_path: Path) -> dict:
    try:
        reader = PdfReader(str(pdf_path))
        text = reader.pages[0].extract_text() or ""
        lines = [l.strip() for l in text.splitlines() if len(l.strip()) > 10]
        title = lines[0][:120] if lines else pdf_path.stem
        year_m = re.search(r'\b(19|20)\d{2}\b', text)
        year = year_m.group(0) if year_m else "Unknown"
        return {"title": title, "author": "Unknown", "year": year}
    except Exception:
        return {"title": pdf_path.stem, "author": "Unknown", "year": "Unknown"}


# ── Core rename logic ─────────────────────────────────────────────────────────

def resolve_metadata(pdf_path: Path) -> dict:
    log.info(f"Processing: {pdf_path.name}")

    # 1. arXiv
    arxiv_id = extract_arxiv_id(pdf_path)
    if arxiv_id:
        log.info(f"  arXiv ID found: {arxiv_id}")
        meta = fetch_arxiv(arxiv_id)
        if meta:
            log.info(f"  ✓ arXiv: {meta}")
            return meta

    # 2. Semantic Scholar
    try:
        reader = PdfReader(str(pdf_path))
        lines = [l.strip() for l in (reader.pages[0].extract_text() or "").splitlines() if len(l.strip()) > 15]
        hint = lines[0] if lines else ""
    except Exception:
        hint = ""

    if hint:
        log.info(f"  Trying Semantic Scholar: {hint[:60]}")
        meta = fetch_semantic_scholar(hint)
        if meta:
            log.info(f"  ✓ Semantic Scholar: {meta}")
            return meta

    # 3. Embedded metadata
    meta = extract_pdf_metadata(pdf_path)
    if meta:
        log.info(f"  ✓ PDF metadata: {meta}")
        return meta

    # 4. Fallback
    meta = extract_text_fallback(pdf_path)
    log.warning(f"  ⚠ Fallback used: {meta}")
    return meta


def rename_pdf(pdf_path: Path, output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    meta = resolve_metadata(pdf_path)
    dest_name = safe_filename(meta["author"], meta["year"], meta["title"])
    dest = output_dir / dest_name
    if dest.exists():
        log.info(f"  ⏭ Skipped (already exists): {dest.name}\n")
        notify_duplicate(pdf_path, dest)
        return
    pdf_path.rename(dest)
    log.info(f"  → {dest.name}\n")
    notify(dest)


# ── Watchdog handler ──────────────────────────────────────────────────────────

class PaperHandler(FileSystemEventHandler):
    def __init__(self, output_dir: Path):
        self.output_dir = output_dir

    def on_created(self, event):
        if event.is_directory:
            return
        path = Path(event.src_path)
        if path.suffix.lower() == ".pdf":
            time.sleep(1)  # wait for file write to finish
            try:
                rename_pdf(path, self.output_dir)
            except Exception as e:
                log.error(f"Failed to process {path.name}: {e}")
                notify_error(path, e)


# ── CLI ───────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="accio — watch a folder and auto-rename academic PDFs.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python accio.py
  python accio.py -i ~/Downloads/papers -o ~/Library/Papers
  python accio.py -i ~/papers -o ~/renamed --process-existing
        """,
    )
    parser.add_argument(
        "--input", "-i",
        type=Path,
        default=Path.home() / "papers",
        metavar="DIR",
        help="Directory to watch (default: ~/papers)",
    )
    parser.add_argument(
        "--output", "-o",
        type=Path,
        default=Path.home() / "papers_renamed",
        metavar="DIR",
        help="Directory for renamed PDFs (default: ~/papers_renamed)",
    )
    parser.add_argument(
        "--process-existing",
        action="store_true",
        help="Also rename PDFs already in the input directory on startup",
    )
    args = parser.parse_args()

    input_dir: Path = args.input.expanduser().resolve()
    output_dir: Path = args.output.expanduser().resolve()

    if not input_dir.exists():
        log.error(f"Input directory does not exist: {input_dir}")
        return

    log.info(f"Input  : {input_dir}")
    log.info(f"Output : {output_dir}")

    if args.process_existing:
        existing = list(input_dir.glob("*.pdf"))
        if existing:
            log.info(f"Processing {len(existing)} existing PDF(s)...")
            for pdf in existing:
                try:
                    rename_pdf(pdf, output_dir)
                except Exception as e:
                    log.error(f"Failed: {pdf.name}: {e}")

    handler = PaperHandler(output_dir)
    observer = Observer()
    observer.schedule(handler, str(input_dir), recursive=False)
    observer.start()
    log.info("Watching for new PDFs... (Ctrl+C to stop)\n")

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        observer.stop()
        log.info("Stopped.")
    observer.join()


if __name__ == "__main__":
    main()
