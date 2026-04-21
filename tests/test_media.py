"""Tests for pester.sync.media — PDF text extraction + image OCR."""

from __future__ import annotations

import json
import sys
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch


# ── PDF extraction tests ─────────────────────────────────────────────


class TestPdfExtraction:
    """Tests for extract_pdf_text()."""

    def test_extract_pdf_text_happy_path(self, tmp_path: Path):
        """PDF with text returns extracted content."""
        # Create a mock fitz module
        mock_fitz = MagicMock()
        mock_page = MagicMock()
        mock_page.get_text.return_value = "Hello from PDF page 1"

        mock_doc = MagicMock()
        mock_doc.__iter__ = lambda self: iter([mock_page])
        mock_fitz.open.return_value = mock_doc

        with patch.dict(sys.modules, {"fitz": mock_fitz}):
            with patch("pester.sync.media.HAS_PYMUPDF", True):
                from pester.sync.media import extract_pdf_text

                result = extract_pdf_text(tmp_path / "test.pdf")

        assert result == "Hello from PDF page 1"

    def test_extract_pdf_text_corrupt_file(self, tmp_path: Path):
        """Corrupt/unreadable PDF returns None."""
        mock_fitz = MagicMock()
        mock_fitz.open.side_effect = RuntimeError("corrupt file")

        with patch.dict(sys.modules, {"fitz": mock_fitz}):
            with patch("pester.sync.media.HAS_PYMUPDF", True):
                from pester.sync.media import extract_pdf_text

                result = extract_pdf_text(tmp_path / "corrupt.pdf")

        assert result is None


# ── Image OCR tests ──────────────────────────────────────────────────


class TestImageOcr:
    """Tests for extract_image_text()."""

    def test_extract_image_text_happy_path(self, tmp_path: Path):
        """OpenAI vision API returns extracted text."""
        from pester.sync.media import extract_image_text

        # Create a small dummy image file
        image_path = tmp_path / "test.jpg"
        image_path.write_bytes(b"\xff\xd8\xff\xe0" + b"\x00" * 100)

        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = "Invoice #12345"
        mock_client.chat.completions.create.return_value = mock_response

        config = {}

        with patch("pester.llm._shared.create_client", return_value=mock_client):
            result = extract_image_text(image_path, config)

        assert result == "Invoice #12345"
        mock_client.chat.completions.create.assert_called_once()

    def test_extract_image_text_api_failure(self, tmp_path: Path):
        """API error returns None gracefully."""
        from pester.sync.media import extract_image_text

        image_path = tmp_path / "test.jpg"
        image_path.write_bytes(b"\xff\xd8\xff\xe0" + b"\x00" * 100)

        mock_client = MagicMock()
        mock_client.chat.completions.create.side_effect = Exception("API timeout")

        config = {}

        with patch("pester.llm._shared.create_client", return_value=mock_client):
            result = extract_image_text(image_path, config)

        assert result is None


# ── Daily cap tests ──────────────────────────────────────────────────


class TestDailyCap:
    """Tests for check_daily_cap() and increment_daily_counter()."""

    def test_daily_cap_under_limit(self, tmp_path: Path):
        """Counter at 5 with cap 50 returns True (under cap)."""
        from pester.sync.media import check_daily_cap

        counter_data = {"date": date.today().isoformat(), "count": 5}
        (tmp_path / "ocr_counter.json").write_text(json.dumps(counter_data))

        assert check_daily_cap(tmp_path, cap=50) is True

    def test_daily_cap_exceeded(self, tmp_path: Path):
        """Counter at 50 with cap 50 returns False (cap reached)."""
        from pester.sync.media import check_daily_cap

        counter_data = {"date": date.today().isoformat(), "count": 50}
        (tmp_path / "ocr_counter.json").write_text(json.dumps(counter_data))

        assert check_daily_cap(tmp_path, cap=50) is False

    def test_daily_cap_resets_on_new_day(self, tmp_path: Path):
        """Counter from yesterday resets, so returns True."""
        from pester.sync.media import check_daily_cap

        yesterday = (date.today() - timedelta(days=1)).isoformat()
        counter_data = {"date": yesterday, "count": 999}
        (tmp_path / "ocr_counter.json").write_text(json.dumps(counter_data))

        assert check_daily_cap(tmp_path, cap=50) is True


# ── Extracted stub tests ─────────────────────────────────────────────


class TestExtractedStub:
    """Tests for create_extracted_stub()."""

    def test_create_stub_with_text(self, tmp_path: Path):
        """Stub file created with frontmatter and extracted text."""
        from pester.sync.media import create_extracted_stub

        vault_path = tmp_path / "vault"
        vault_path.mkdir()

        result = create_extracted_stub(
            vault_path=vault_path,
            source_filename="telegram-chat-42.jpg",
            extracted_text="Hello from image",
            media_type="photo",
            msg_id=42,
        )

        assert result.exists()
        content = result.read_text(encoding="utf-8")
        assert "type: reference" in content
        assert "source: telegram" in content
        assert "media_type: photo" in content
        assert "original_file: ../assets/telegram-chat-42.jpg" in content
        assert "has_text: true" in content
        assert "Hello from image" in content

    def test_create_stub_without_text(self, tmp_path: Path):
        """Stub file created with fallback message when extraction fails."""
        from pester.sync.media import create_extracted_stub

        vault_path = tmp_path / "vault"
        vault_path.mkdir()

        result = create_extracted_stub(
            vault_path=vault_path,
            source_filename="telegram-chat-99.pdf",
            extracted_text=None,
            media_type="pdf",
            msg_id=99,
        )

        assert result.exists()
        content = result.read_text(encoding="utf-8")
        assert "has_text: false" in content
        assert "[Text extraction failed. Original file: ../assets/telegram-chat-99.pdf]" in content
