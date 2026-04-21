"""Tests for pester.rag.chunker — pure-Python markdown chunking logic.

All tests marked @pytest.mark.search (RAG code, but no ChromaDB/ONNX needed).
"""

from __future__ import annotations

import pytest

from pester.rag.chunker import (
    CHUNK_SIZE_DEFAULT,
    MAX_CHUNKS_PER_FILE,
    chunk_file,
    is_table_chunk,
    parse_frontmatter,
    process_table_chunk,
    split_by_paragraphs,
)
from tests.conftest import FIXTURES_DIR


# ── Helpers ──────────────────────────────────────────────────────────────────


def _read_fixture(rel: str) -> str:
    return (FIXTURES_DIR / rel).read_text(encoding="utf-8")


# ── Frontmatter tests ───────────────────────────────────────────────────────


@pytest.mark.search
def test_frontmatter_valid():
    """Parse simple.md and verify all frontmatter fields."""
    content = _read_fixture("simple.md")
    meta, body = parse_frontmatter(content)

    assert meta["title"] == "Simple Document"
    assert meta["type"] == "reference"
    assert meta["status"] == "active"
    assert meta["tags"] == "test"
    assert meta["date"] == "2026-03-15"
    assert "simple document for testing" in body.lower()


@pytest.mark.search
def test_frontmatter_missing():
    """Text without frontmatter returns empty metadata and full body."""
    text = "No frontmatter here.\nJust plain markdown."
    meta, body = parse_frontmatter(text)

    assert meta == {}
    assert body == text


@pytest.mark.search
def test_frontmatter_malformed():
    """Malformed YAML frontmatter falls back gracefully to empty metadata."""
    content = _read_fixture("malformed-frontmatter.md")
    meta, body = parse_frontmatter(content)

    # Malformed YAML → empty metadata, entire content as body
    assert meta == {}
    assert body == content


@pytest.mark.search
def test_frontmatter_tags_list_and_string():
    """Tags given as YAML list become comma-joined string; scalar stays as-is."""
    list_content = "---\ntitle: T\ntags: [a, b, c]\n---\nBody.\n"
    meta_list, _ = parse_frontmatter(list_content)
    assert meta_list["tags"] == "a,b,c"

    string_content = "---\ntitle: T\ntags: single-tag\n---\nBody.\n"
    meta_str, _ = parse_frontmatter(string_content)
    assert meta_str["tags"] == "single-tag"


# ── chunk_file tests ────────────────────────────────────────────────────────


@pytest.mark.search
def test_chunk_whole_file():
    """simple.md body < CHUNK_SIZE_WHOLE_FILE → produces exactly 1 chunk."""
    content = _read_fixture("simple.md")
    chunks = chunk_file("simple.md", content)

    assert len(chunks) == 1
    assert chunks[0]["metadata"]["file_path"] == "simple.md"
    assert chunks[0]["metadata"]["title"] == "Simple Document"
    assert chunks[0]["id"] == "simple.md::0"


@pytest.mark.search
def test_chunk_by_sections():
    """Document with ## headings and body > CHUNK_SIZE_WHOLE_FILE produces per-section chunks."""
    # sections.md body is only ~975 bytes (< 2000), so it becomes a single chunk.
    # Synthesize a document large enough to trigger section-level splitting.
    sections = [
        "## Background\n\n" + "Background context. " * 80,
        "## Decision\n\n" + "We decided to go with option A. " * 80,
        "## Implementation\n\n" + "The implementation follows standard patterns. " * 80,
        "## Consequences\n\n" + "This means updating the pipeline. " * 80,
    ]
    body = "Intro paragraph.\n\n" + "\n\n".join(sections)
    content = f"---\ntitle: Big Sectioned\n---\n{body}\n"

    chunks = chunk_file("big-sectioned.md", content)

    assert len(chunks) >= 4
    texts = " ".join(c["text"] for c in chunks)
    assert "Background" in texts
    assert "Decision" in texts
    assert "Implementation" in texts
    assert "Consequences" in texts


@pytest.mark.search
def test_chunk_by_paragraphs():
    """Large text without ## headings splits by paragraphs."""
    # Build text with many paragraphs, exceeding CHUNK_SIZE_WHOLE_FILE
    paras = ["Paragraph number %d. " % i + "x" * 200 for i in range(20)]
    body = "\n\n".join(paras)
    content = f"---\ntitle: Big Doc\n---\n{body}\n"

    chunks = chunk_file("big.md", content)

    # Should produce more than one chunk (paragraphs grouped up to chunk_size)
    assert len(chunks) > 1
    # Each chunk text should be non-empty
    for c in chunks:
        assert len(c["text"].strip()) > 0


@pytest.mark.search
def test_chunk_transcript():
    """Files in reference/transcripts/ path get CHUNK_SIZE_TRANSCRIPT and source_type=transcript."""
    content = _read_fixture("reference/transcripts/test-meeting.md")
    rel_path = "reference/transcripts/test-meeting.md"
    chunks = chunk_file(rel_path, content)

    assert len(chunks) >= 1
    assert chunks[0]["metadata"]["source_type"] == "transcript"
    assert chunks[0]["metadata"]["type"] == "transcript"


@pytest.mark.search
def test_strip_base64():
    """base64-images.md has inline and reference base64 images replaced with placeholders."""
    content = _read_fixture("base64-images.md")
    chunks = chunk_file("base64-images.md", content)

    assert len(chunks) >= 1
    combined = " ".join(c["text"] for c in chunks)
    # base64 data should be stripped
    assert "iVBOR" not in combined
    assert "PHN2Zy" not in combined
    # Placeholder text should be present
    assert "image" in combined.lower()


@pytest.mark.search
def test_skip_tiny_file():
    """tiny.md body is below MIN_CONTENT_BYTES → returns empty list."""
    content = _read_fixture("tiny.md")
    chunks = chunk_file("tiny.md", content)
    assert chunks == []


@pytest.mark.search
def test_chunk_ceiling():
    """Content producing > MAX_CHUNKS_PER_FILE chunks gets truncated."""
    # Create many small sections to exceed the ceiling
    sections = []
    for i in range(MAX_CHUNKS_PER_FILE + 50):
        sections.append(f"## Section {i}\n\n{'Content ' * 30} for section {i}.")
    body = "\n\n".join(sections)
    content = f"---\ntitle: Huge Doc\n---\n{body}\n"

    chunks = chunk_file("huge.md", content)

    assert len(chunks) <= MAX_CHUNKS_PER_FILE


# ── Table detection & parsing ────────────────────────────────────────────────


@pytest.mark.search
def test_table_detection():
    """is_table_chunk correctly distinguishes table text from non-table text."""
    table_text = "| A | B |\n|---|---|\n| 1 | 2 |\n| 3 | 4 |"
    assert is_table_chunk(table_text) is True

    non_table = "This is just a paragraph.\nNo pipes here.\nPlain text."
    assert is_table_chunk(non_table) is False

    # Edge case: empty string
    assert is_table_chunk("") is False


@pytest.mark.search
def test_table_cat_a_full():
    """table-full.md with table_mode: full gets full linearization (Category A)."""
    content = _read_fixture("table-full.md")
    meta, body = parse_frontmatter(content)
    chunks = chunk_file("table-full.md", content)

    assert len(chunks) >= 1
    combined = " ".join(c["text"] for c in chunks)
    # Full linearization → all rows converted to prose
    assert "Q1 2025" in combined
    assert "Q2 2025" in combined
    assert "Q3 2025" in combined
    assert "Q4 2025" in combined


@pytest.mark.search
def test_table_cat_b_summary():
    """table-summary.md (12 rows, no table_mode) gets Category B summary with max 10 sample rows."""
    content = _read_fixture("table-summary.md")
    chunks = chunk_file("table-summary.md", content)

    assert len(chunks) >= 1
    combined = " ".join(c["text"] for c in chunks)
    # Summary header should be present
    assert "12" in combined or "rows" in combined.lower()
    # Should include "Showing first" since there are >10 rows
    assert "Showing first" in combined or "first 10" in combined


@pytest.mark.search
def test_table_cat_a_split():
    """Very large Category A table that exceeds chunk_size gets split into multiple chunks."""
    # Build a large table with many rows
    header = "| Name | Description | Value | Notes |"
    separator = "|------|-------------|-------|-------|"
    rows = [
        f"| Item{i} | {'Description ' * 10}{i} | {i * 100} | {'Note ' * 10}{i} |" for i in range(50)
    ]
    table_text = "\n".join([header, separator] + rows)

    result = process_table_chunk(
        table_text,
        file_path="big-table.md",
        section_heading="Big Table",
        chunk_size=CHUNK_SIZE_DEFAULT,
        metadata={"table_mode": "full"},
        language="en",
    )

    # With 50 long rows and full linearization, should produce multiple chunks
    assert len(result) > 1
    # Each chunk should contain the header block
    for chunk in result:
        assert "Big Table" in chunk
        assert "Columns:" in chunk


@pytest.mark.search
def test_table_frontmatter_mode():
    """Frontmatter table_mode: full triggers Category A linearization."""
    content = _read_fixture("table-full.md")
    meta, body = parse_frontmatter(content)

    assert meta["table_mode"] == "full"

    # When chunked, the full table mode applies
    chunks = chunk_file("table-full.md", content)
    assert len(chunks) >= 1
    # All data rows should be linearized (Category A)
    combined = " ".join(c["text"] for c in chunks)
    assert "Q1 2025" in combined
    assert "Q4 2025" in combined


@pytest.mark.search
def test_table_config_list():
    """Passing file path in table_full_files triggers Category A for that file."""
    content = _read_fixture("table-summary.md")

    # Without table_full_files → Category B (summary)
    chunks_b = chunk_file("table-summary.md", content)

    # With table_full_files → Category A (full linearization)
    chunks_a = chunk_file("table-summary.md", content, table_full_files=["table-summary.md"])

    combined_a = " ".join(c["text"] for c in chunks_a)
    combined_b = " ".join(c["text"] for c in chunks_b)

    # Category A should NOT have "Showing first" since all rows are linearized
    # Category B should have "Showing first" since >10 rows are truncated
    assert "Showing first" in combined_b
    # Category A fully linearizes — should include later rows like Liam (row 12)
    assert "Liam" in combined_a


# ── Language-aware table descriptions ────────────────────────────────────────


@pytest.mark.search
def test_table_desc_english():
    """Default language='en' produces English table descriptions."""
    table_text = "| Col1 | Col2 |\n|------|------|\n| a | b |\n| c | d |"
    result = process_table_chunk(
        table_text,
        file_path="test.md",
        section_heading="Test Table",
        chunk_size=CHUNK_SIZE_DEFAULT,
        language="en",
    )

    combined = " ".join(result)
    assert "Table:" in combined
    assert "Columns:" in combined


@pytest.mark.search
def test_table_desc_russian():
    """language='ru' produces Russian table descriptions."""
    table_text = "| Col1 | Col2 |\n|------|------|\n| a | b |\n| c | d |"
    result = process_table_chunk(
        table_text,
        file_path="test.md",
        section_heading="Test Table",
        chunk_size=CHUNK_SIZE_DEFAULT,
        language="ru",
    )

    combined = " ".join(result)
    assert "Таблица:" in combined
    assert "Колонки:" in combined


# ── Paragraph splitting ──────────────────────────────────────────────────────


@pytest.mark.search
def test_para_split_normal():
    """split_by_paragraphs groups paragraphs up to target_size bytes."""
    p1 = "Short paragraph one."
    p2 = "Short paragraph two."
    p3 = "Short paragraph three."
    text = f"{p1}\n\n{p2}\n\n{p3}"

    # With a large target, everything fits in one chunk
    chunks_big = split_by_paragraphs(text, 10000)
    assert len(chunks_big) == 1
    assert p1 in chunks_big[0]
    assert p2 in chunks_big[0]
    assert p3 in chunks_big[0]

    # With a small target, each paragraph becomes its own chunk
    chunks_small = split_by_paragraphs(text, 25)
    assert len(chunks_small) >= 2
    # Verify paragraphs are preserved (not split mid-word)
    all_text = " ".join(chunks_small)
    assert "Short paragraph" in all_text


@pytest.mark.search
def test_hard_split_utf8():
    """Verify UTF-8 boundary safety: multibyte characters are never split mid-byte."""
    # Cyrillic char "Я" = 2 bytes, emoji "🔥" = 4 bytes
    # Build a long string of multibyte characters
    text = "Я" * 500 + "🔥" * 200  # 1000 + 800 = 1800 bytes
    target_size = 100

    chunks = split_by_paragraphs(text, target_size)

    # Every chunk must be valid UTF-8 (no decode errors)
    for chunk in chunks:
        encoded = chunk.encode("utf-8")
        decoded = encoded.decode("utf-8")
        assert decoded == chunk

    # Reassembled text should match original
    joined = "".join(chunks)
    assert joined == text

    # Each chunk should be at most target_size bytes
    for chunk in chunks:
        assert len(chunk.encode("utf-8")) <= target_size


# ── Configurable chunk size ───────────────────────────────────────────────


@pytest.mark.search
def test_chunk_size_from_config():
    """Passing chunk_size overrides CHUNK_SIZE_DEFAULT: a 3000-byte doc stays whole at 4000."""
    # Build a document body that is ~3000 bytes (larger than default 2500 but under 4000)
    body = "A" * 3000
    content = f"---\ntitle: Sized Doc\n---\n{body}\n"

    # With default chunk_size (2500), this should be split since body > CHUNK_SIZE_WHOLE_FILE
    chunks_default = chunk_file("sized.md", content)
    assert len(chunks_default) > 1, "Expected splitting at default chunk size"

    # With chunk_size=4000, the entire body fits in one chunk
    chunks_large = chunk_file("sized.md", content, chunk_size=4000)
    assert len(chunks_large) == 1, "Expected single chunk with chunk_size=4000"
