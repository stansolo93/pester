"""Markdown chunker for pester RAG pipeline.

Multi-level splitting: whole-file → sections (## headings) → paragraphs.
Supports table linearization, base64 stripping, and YAML frontmatter.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

import yaml

from pester.core.config import EXCLUDE_DIRS, SKIP_FILES

log = logging.getLogger(__name__)

# ── RAG-internal constants (not user-configurable) ────────────────────────────

CHUNK_SIZE_WHOLE_FILE = 2000  # bytes — index whole file if body is smaller
CHUNK_SIZE_DEFAULT = 2500  # bytes — max chunk size for documents
CHUNK_SIZE_TRANSCRIPT = 3000  # bytes — max chunk size for transcripts
MAX_CHUNKS_PER_FILE = 100  # ceiling with logging
MIN_CONTENT_BYTES = 100  # skip files below this

TABLE_LINE_THRESHOLD = 0.5  # fraction of lines with | to detect tables
TABLE_MAX_LINEARIZED_ROWS = 10  # max rows for Category B summary

# ── Language-aware table descriptions ─────────────────────────────────────────

_TABLE_STRINGS: dict[str, dict[str, str]] = {
    "en": {
        "table": "Table: {n} rows.",
        "columns": "Columns: {cols}.",
        "total": "Total: {total}.",
        "showing_first": "Showing first {shown} of {total} rows. Full table: vault_get_document('{path}')",
        "full_table": "\nFull table: vault_get_document('{path}')",
    },
    "ru": {
        "table": "Таблица: {n} строк.",
        "columns": "Колонки: {cols}.",
        "total": "Итого: {total}.",
        "showing_first": (
            "Показаны первые {shown} из {total} строк. Полная таблица: vault_get_document('{path}')"
        ),
        "full_table": "\nПолная таблица: vault_get_document('{path}')",
    },
}


def _get_strings(language: str) -> dict[str, str]:
    """Get table description strings for the given language, defaulting to English."""
    return _TABLE_STRINGS.get(language, _TABLE_STRINGS["en"])


# ── Content cleaning ──────────────────────────────────────────────────────────

_BASE64_RE = re.compile(
    r"\[([^\]]*)\]:\s*<data:image/[^>]+>",  # reference-style: [img]: <data:...>
)
_BASE64_INLINE_RE = re.compile(
    r"!\[([^\]]*)\]\(data:image/[^)]+\)",  # inline: ![alt](data:...)
)


def _strip_base64(text: str) -> str:
    """Remove base64 data URIs from markdown (both reference and inline styles)."""
    text = _BASE64_RE.sub(r"[\1]: [image]", text)
    text = _BASE64_INLINE_RE.sub(r"![\1](image)", text)
    return text


# ── Frontmatter ───────────────────────────────────────────────────────────────

_FM_RE = re.compile(r"\A---\s*\n(.*?\n)---\s*\n", re.DOTALL)


def parse_frontmatter(content: str) -> tuple[dict, str]:
    """Parse YAML frontmatter. Returns (metadata, body)."""
    m = _FM_RE.match(content)
    if not m:
        return {}, content

    try:
        raw = yaml.safe_load(m.group(1)) or {}
    except yaml.YAMLError:
        return {}, content

    meta: dict = {}
    meta["title"] = raw.get("title")
    meta["type"] = raw.get("type", "reference")
    meta["status"] = raw.get("status", "active")

    tags = raw.get("tags", [])
    meta["tags"] = ",".join(str(t) for t in tags) if isinstance(tags, list) else str(tags)

    date = raw.get("date")
    meta["date"] = str(date) if date else None

    meta["table_mode"] = raw.get("table_mode")

    body = content[m.end() :]
    return meta, body


# ── Paragraph splitting ───────────────────────────────────────────────────────


def _hard_split(text: str, target_size: int) -> list[str]:
    """Split an oversized paragraph by sentence boundaries ('. '), then by hard cut."""
    sentences = re.split(r"(?<=\. )", text)
    if len(sentences) > 1:
        chunks: list[str] = []
        current = ""
        for s in sentences:
            if current and len((current + s).encode("utf-8")) > target_size:
                chunks.append(current.rstrip())
                current = s
            else:
                current += s
        if current:
            chunks.append(current.rstrip())
        result: list[str] = []
        for ch in chunks:
            if len(ch.encode("utf-8")) > target_size:
                result.extend(_hard_split_bytes(ch, target_size))
            else:
                result.append(ch)
        return result
    return _hard_split_bytes(text, target_size)


def _hard_split_bytes(text: str, target_size: int) -> list[str]:
    """Last-resort byte-level split that respects UTF-8 character boundaries."""
    encoded = text.encode("utf-8")
    parts: list[str] = []
    start = 0
    while start < len(encoded):
        end = min(start + target_size, len(encoded))
        while end < len(encoded) and (encoded[end] & 0xC0) == 0x80:
            end -= 1
        parts.append(encoded[start:end].decode("utf-8"))
        start = end
    return parts


def split_by_paragraphs(text: str, target_size: int) -> list[str]:
    r"""Split text on \\n\\n boundaries, grouping paragraphs up to target_size bytes."""
    paragraphs = text.split("\n\n")
    chunks: list[str] = []
    current: list[str] = []
    current_size = 0

    for para in paragraphs:
        para_size = len(para.encode("utf-8"))

        if para_size > target_size:
            if current:
                chunks.append("\n\n".join(current))
                current = []
                current_size = 0
            chunks.extend(_hard_split(para, target_size))
            continue

        if current and current_size + 2 + para_size > target_size:
            chunks.append("\n\n".join(current))
            current = [para]
            current_size = para_size
        else:
            current.append(para)
            current_size += (2 if current_size else 0) + para_size

    if current:
        chunks.append("\n\n".join(current))

    return chunks


# ── Table handling ────────────────────────────────────────────────────────────

_TOTAL_RE = re.compile(r"\b(total|итого|всего|sum)\b", re.IGNORECASE)
_SEP_RE = re.compile(r"^[-:|  ]+$")


def is_table_chunk(text: str) -> bool:
    """Detect whether text is primarily a markdown table."""
    lines = [line for line in text.strip().split("\n") if line.strip()]
    if not lines:
        return False
    table_lines = sum(1 for line in lines if "|" in line)
    return table_lines / len(lines) > TABLE_LINE_THRESHOLD


def _split_row(line: str) -> list[str]:
    """Split a markdown table row by | and strip each cell."""
    parts = line.split("|")
    if parts and not parts[0].strip():
        parts = parts[1:]
    if parts and not parts[-1].strip():
        parts = parts[:-1]
    return [c.strip() for c in parts]


def parse_table(text: str) -> tuple[list[str], list[list[str]], str | None]:
    """Parse markdown table → (headers, data_rows, total_row_prose | None)."""
    lines = [line for line in text.strip().split("\n") if line.strip() and "|" in line]
    if len(lines) < 2:
        return [], [], None

    headers = _split_row(lines[0])

    rows: list[list[str]] = []
    for line in lines[1:]:
        cells = _split_row(line)
        if all(_SEP_RE.match(c) for c in cells if c):
            continue
        rows.append(cells)

    total_row: str | None = None
    if rows:
        last_text = " ".join(rows[-1]).lower()
        if _TOTAL_RE.search(last_text):
            total_cells = rows.pop()
            total_row = linearize_rows(headers, [total_cells])

    return headers, rows, total_row


def linearize_rows(headers: list[str], rows: list[list[str]]) -> str:
    """Each row as prose: 'col1: val1, col2: val2'."""
    result: list[str] = []
    for row in rows:
        parts = []
        for i, cell in enumerate(row):
            if not cell or cell == "—":
                continue
            h = headers[i] if i < len(headers) else f"col{i}"
            parts.append(f"{h}: {cell}")
        if parts:
            result.append(", ".join(parts))
    return "\n".join(result)


def _uses_full_linearization(
    file_path: str,
    metadata: dict,
    table_full_files: list[str] | None = None,
) -> bool:
    """Check if file should use full table linearization (Category A).

    Frontmatter `table_mode: full` takes precedence over config list.
    """
    if metadata.get("table_mode") == "full":
        return True
    if table_full_files and file_path in table_full_files:
        return True
    return False


def process_table_chunk(
    text: str,
    file_path: str,
    section_heading: str,
    chunk_size: int,
    metadata: dict | None = None,
    language: str = "en",
    table_full_files: list[str] | None = None,
) -> list[str]:
    """Process a table chunk into linearized text chunks (Category A or B)."""
    headers, rows, total_row = parse_table(text)
    if not headers or not rows:
        return [text]

    strings = _get_strings(language)
    n = len(rows)
    cols = ", ".join(headers)
    is_cat_a = _uses_full_linearization(file_path, metadata or {}, table_full_files)

    header_parts = [
        f"{section_heading}. {strings['table'].format(n=n)}",
        strings["columns"].format(cols=cols),
    ]
    if total_row:
        header_parts.append(strings["total"].format(total=total_row))
    header_block = "\n".join(header_parts)
    ref_line = strings["full_table"].format(path=file_path)

    if is_cat_a:
        all_linearized = linearize_rows(headers, rows)
        full_text = header_block + "\n\n" + all_linearized + ref_line

        if len(full_text.encode("utf-8")) <= chunk_size:
            return [full_text]

        row_lines = all_linearized.split("\n")
        parts: list[str] = []
        current: list[str] = []
        current_size = len(header_block.encode("utf-8")) + len(ref_line.encode("utf-8")) + 4

        for line in row_lines:
            line_size = len(line.encode("utf-8")) + 1
            if current and current_size + line_size > chunk_size:
                part_text = header_block + "\n\n" + "\n".join(current) + ref_line
                parts.append(part_text)
                current = [line]
                current_size = (
                    len(header_block.encode("utf-8"))
                    + len(ref_line.encode("utf-8"))
                    + 4
                    + line_size
                )
            else:
                current.append(line)
                current_size += line_size

        if current:
            part_text = header_block + "\n\n" + "\n".join(current) + ref_line
            parts.append(part_text)

        return parts

    # Category B — summary + sample
    sample_rows = rows[:TABLE_MAX_LINEARIZED_ROWS]
    sample_linearized = linearize_rows(headers, sample_rows)

    summary_parts = list(header_parts)
    if n > TABLE_MAX_LINEARIZED_ROWS:
        summary_parts.append(
            strings["showing_first"].format(shown=len(sample_rows), total=n, path=file_path)
        )

    result = ["\n".join(summary_parts)]
    if sample_linearized.strip():
        result.append(sample_linearized)
    return result


# ── Prefix deduplication ──────────────────────────────────────────────────────


def _make_text(prefix: str, content: str) -> str:
    """Combine prefix + content, removing duplicated heading from content start."""
    stripped = content.lstrip("\n")
    first_line = stripped.split("\n", 1)[0].strip()

    if first_line.startswith("## ") or first_line.startswith("# "):
        heading_text = first_line.lstrip("#").strip()
        if heading_text and heading_text in prefix:
            stripped = stripped[len(first_line) :].lstrip("\n")

    combined = prefix + "\n" + stripped if stripped.strip() else prefix
    return combined.strip()


# ── Public API ────────────────────────────────────────────────────────────────


def chunk_file(
    rel_path: str,
    content: str,
    *,
    language: str = "en",
    table_full_files: list[str] | None = None,
    chunk_size: int | None = None,
) -> list[dict]:
    """Chunk a single file into a list of chunk dicts.

    Args:
        rel_path: Path relative to vault root.
        content: Raw markdown file contents.
        language: Language for table descriptions ('en' or 'ru').
        table_full_files: List of relative paths that get full table linearization.
        chunk_size: Override default max chunk size in bytes.

    Returns:
        List of chunk dicts with 'id', 'text', and 'metadata' keys.
    """
    meta, body = parse_frontmatter(content)
    body = _strip_base64(body)

    if len(body.encode("utf-8")) < MIN_CONTENT_BYTES:
        return []

    title = meta.get("title") or Path(rel_path).stem
    is_transcript = "reference/transcripts/" in rel_path
    if chunk_size:
        effective_chunk_size = chunk_size
    else:
        effective_chunk_size = CHUNK_SIZE_TRANSCRIPT if is_transcript else CHUNK_SIZE_DEFAULT
    source_type = "transcript" if is_transcript else "document"

    base_meta = {
        "file_path": rel_path,
        "title": title,
        "type": meta.get("type", "reference"),
        "status": meta.get("status", "active"),
        "tags": meta.get("tags", ""),
        "date": meta.get("date"),
        "source_type": source_type,
    }

    body_size = len(body.encode("utf-8"))
    raw_chunks: list[str] = []

    table_kwargs = {
        "language": language,
        "table_full_files": table_full_files,
    }

    if body_size <= CHUNK_SIZE_WHOLE_FILE:
        if is_table_chunk(body):
            for tbl_chunk in process_table_chunk(
                body, rel_path, title, effective_chunk_size, meta, **table_kwargs
            ):
                raw_chunks.append(tbl_chunk)
        else:
            raw_chunks = [_make_text(title, body)]

    elif "\n## " in body:
        parts = body.split("\n## ")
        intro = parts[0]
        sections = parts[1:]

        if intro.strip():
            intro_text = _make_text(title, intro)
            if len(intro_text.encode("utf-8")) > effective_chunk_size:
                for sub in split_by_paragraphs(intro_text, effective_chunk_size):
                    raw_chunks.append(sub)
            else:
                raw_chunks.append(intro_text)

        for sec in sections:
            heading_line, _, sec_body = sec.partition("\n")
            heading = heading_line.strip()
            prefix = f"{title}. {heading}"
            sec_content = sec_body.strip()
            prefix_overhead = len(prefix.encode("utf-8")) + 1

            if is_table_chunk(sec_content):
                full_sec = f"## {sec}"
                for tbl_chunk in process_table_chunk(
                    full_sec, rel_path, prefix, effective_chunk_size, meta, **table_kwargs
                ):
                    raw_chunks.append(tbl_chunk)
            elif len(sec_content.encode("utf-8")) > effective_chunk_size - prefix_overhead:
                for sub in split_by_paragraphs(sec_content, effective_chunk_size - prefix_overhead):
                    raw_chunks.append(_make_text(prefix, sub))
            else:
                raw_chunks.append(_make_text(prefix, sec_content))

    else:
        prefix_overhead = len(title.encode("utf-8")) + 1
        if is_table_chunk(body):
            for tbl_chunk in process_table_chunk(
                body, rel_path, title, effective_chunk_size, meta, **table_kwargs
            ):
                raw_chunks.append(tbl_chunk)
        else:
            for sub in split_by_paragraphs(body, effective_chunk_size - prefix_overhead):
                raw_chunks.append(_make_text(title, sub))

    # Ceiling
    if len(raw_chunks) > MAX_CHUNKS_PER_FILE:
        log.warning(
            "File %s hit chunk ceiling: %d chunks truncated to %d",
            rel_path,
            len(raw_chunks),
            MAX_CHUNKS_PER_FILE,
        )
        raw_chunks = raw_chunks[:MAX_CHUNKS_PER_FILE]

    # Build chunk dicts — skip empty and prefix-only chunks
    chunks: list[dict] = []
    for i, text in enumerate(raw_chunks):
        if not text.strip() or len(text.encode("utf-8")) < MIN_CONTENT_BYTES:
            continue
        chunks.append(
            {
                "id": f"{rel_path}::{i}",
                "text": text,
                "metadata": {**base_meta, "chunk_index": i},
            }
        )

    return chunks


def chunk_vault(
    vault_path: Path,
    *,
    language: str = "en",
    table_full_files: list[str] | None = None,
    chunk_size: int | None = None,
) -> list[dict]:
    """Walk the vault and chunk all markdown files.

    Delegates to chunk_file() per file.
    """
    vault_path = Path(vault_path)
    all_chunks: list[dict] = []
    processed = 0
    skipped = 0

    skip_set = set(SKIP_FILES)

    for md_file in sorted(vault_path.rglob("*.md")):
        rel = str(md_file.relative_to(vault_path))

        if any(part in EXCLUDE_DIRS for part in md_file.relative_to(vault_path).parts):
            continue

        if rel in skip_set:
            skipped += 1
            continue

        try:
            content = md_file.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as e:
            log.warning("Cannot read %s: %s", rel, e)
            skipped += 1
            continue

        chunks = chunk_file(
            rel,
            content,
            language=language,
            table_full_files=table_full_files,
            chunk_size=chunk_size,
        )
        if not chunks:
            skipped += 1
            continue

        all_chunks.extend(chunks)
        processed += 1

    log.info("Chunked %d files → %d chunks (skipped %d)", processed, len(all_chunks), skipped)
    return all_chunks
