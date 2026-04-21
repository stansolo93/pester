"""Media processing for Telegram sync: PDF text extraction + image OCR."""

from __future__ import annotations

import base64
import json
import logging
from datetime import date, datetime, timezone
from pathlib import Path
from pester.core.config import get_config_value
from pester.core.extras import make_optional_check
from pester.core.vault import atomic_write

HAS_PYMUPDF, require_pymupdf = make_optional_check("fitz", "telegram", label="PDF extraction")

logger = logging.getLogger(__name__)


def extract_pdf_text(file_path: Path) -> str | None:
    """Extract text from a PDF file using pymupdf.

    Returns extracted text or None on failure.
    """
    if not HAS_PYMUPDF:
        logger.debug("pymupdf not installed, skipping PDF extraction")
        return None

    try:
        import fitz

        doc = fitz.open(str(file_path))
        pages: list[str] = []
        for page in doc:
            text = page.get_text()
            if text.strip():
                pages.append(text.strip())
        doc.close()
        return "\n\n".join(pages) if pages else None
    except Exception:
        logger.warning("PDF text extraction failed for %s", file_path.name, exc_info=True)
        return None


def extract_image_text(file_path: Path, config: dict) -> str | None:
    """Use LLM vision API to OCR an image.

    Reads the provider from config via ``sync.telegram.ocr.provider`` (default: openai).
    Returns extracted text or None on failure.
    """
    from pester.llm._shared import create_client

    provider = get_config_value(config, "sync.telegram.ocr.provider", "openai")
    api_key_env = get_config_value(
        config,
        "sync.telegram.ocr.api_key_env",
        "OPENAI_API_KEY" if provider == "openai" else "ANTHROPIC_API_KEY",
    )

    client = create_client(provider, api_key_env, timeout=60)
    if client is None:
        logger.warning("OCR client unavailable (provider=%s, key_env=%s)", provider, api_key_env)
        return None

    # Read and encode the image
    try:
        image_data = file_path.read_bytes()
        b64_image = base64.b64encode(image_data).decode("utf-8")
    except OSError:
        logger.warning("Failed to read image file: %s", file_path.name)
        return None

    # Determine media type from extension
    suffix = file_path.suffix.lower()
    media_type_map = {
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".gif": "image/gif",
        ".webp": "image/webp",
    }
    image_media_type = media_type_map.get(suffix, "image/jpeg")

    system_prompt = (
        "Extract all visible text from this image. "
        "Return only the extracted text, nothing else. "
        "If no text is visible, return empty string."
    )

    try:
        if provider == "anthropic":
            response = client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=4096,
                system=system_prompt,
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "image",
                                "source": {
                                    "type": "base64",
                                    "media_type": image_media_type,
                                    "data": b64_image,
                                },
                            },
                            {
                                "type": "text",
                                "text": "Extract all text from this image.",
                            },
                        ],
                    }
                ],
            )
            text = response.content[0].text if response.content else ""
        else:
            # OpenAI
            response = client.chat.completions.create(
                model="gpt-5.4-nano",
                max_tokens=4096,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "image_url",
                                "image_url": {
                                    "url": f"data:{image_media_type};base64,{b64_image}",
                                },
                            },
                        ],
                    },
                ],
            )
            text = response.choices[0].message.content or ""

        return text.strip() if text.strip() else None
    except Exception:
        logger.warning("OCR API call failed for %s", file_path.name, exc_info=True)
        return None


def check_daily_cap(state_dir: Path, cap: int = 50) -> bool:
    """Check if daily OCR cap has been reached.

    Tracks count in ``state_dir/ocr_counter.json``.
    Returns True if under cap, False if cap reached.
    """
    counter_path = state_dir / "ocr_counter.json"
    today = date.today().isoformat()

    try:
        data = json.loads(counter_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        data = {"date": today, "count": 0}

    # Reset counter on new day
    if data.get("date") != today:
        data = {"date": today, "count": 0}

    return data["count"] < cap


def increment_daily_counter(state_dir: Path) -> None:
    """Increment the daily OCR counter in ``state_dir/ocr_counter.json``."""
    counter_path = state_dir / "ocr_counter.json"
    today = date.today().isoformat()

    try:
        data = json.loads(counter_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        data = {"date": today, "count": 0}

    # Reset counter on new day
    if data.get("date") != today:
        data = {"date": today, "count": 0}

    data["count"] += 1

    state_dir.mkdir(parents=True, exist_ok=True)
    atomic_write(counter_path, json.dumps(data, indent=2))


def create_extracted_stub(
    vault_path: Path,
    source_filename: str,
    extracted_text: str | None,
    media_type: str,
    msg_id: int | str,
) -> Path:
    """Create a .md stub file for extracted media text.

    Writes to ``vault_path/reference/telegram/extracted/``.
    Returns path to the created file.
    """
    extracted_dir = vault_path / "reference" / "telegram" / "extracted"
    extracted_dir.mkdir(parents=True, exist_ok=True)

    stem = Path(source_filename).stem
    base_name = f"{msg_id}-{stem}.md"
    out_path = extracted_dir / base_name

    # Handle filename collisions
    counter = 2
    while out_path.exists():
        out_path = extracted_dir / f"{msg_id}-{stem}-{counter}.md"
        counter += 1

    has_text = extracted_text is not None and len(extracted_text.strip()) > 0
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    lines = [
        "---",
        "type: reference",
        "source: telegram",
        f"media_type: {media_type}",
        f"original_file: ../assets/{source_filename}",
        f"extracted_at: {now}",
        f"has_text: {'true' if has_text else 'false'}",
        "---",
        "",
    ]

    if has_text:
        lines.append(extracted_text)  # type: ignore[arg-type]
    else:
        lines.append(f"[Text extraction failed. Original file: ../assets/{source_filename}]")

    lines.append("")  # trailing newline
    atomic_write(out_path, "\n".join(lines))
    return out_path


def process_media(
    file_path: Path,
    media_type: str,
    msg_id: int | str,
    vault_path: Path,
    config: dict,
    state_dir: Path,
) -> Path | None:
    """Process a downloaded media file: extract text and create a stub.

    Main entry point called by Telegram sync after media download.

    Args:
        file_path: Path to the downloaded media file.
        media_type: ``"pdf"`` or ``"photo"``/``"image"``.
        msg_id: Telegram message ID.
        vault_path: Root of the vault.
        config: Loaded pester config dict.
        state_dir: State directory (``~/.pester/``).

    Returns:
        Path to the created stub file, or None on complete failure.
    """
    extracted_text: str | None = None

    if media_type == "pdf":
        extracted_text = extract_pdf_text(file_path)
    elif media_type in ("photo", "image"):
        ocr_enabled = get_config_value(config, "sync.telegram.ocr.enabled", True)
        if not ocr_enabled:
            logger.debug("OCR disabled in config, skipping image extraction")
        else:
            daily_cap = get_config_value(config, "sync.telegram.ocr.daily_cap", 50)
            if not check_daily_cap(state_dir, daily_cap):
                logger.warning(
                    "Daily OCR cap (%d) reached, skipping image extraction for %s",
                    daily_cap,
                    file_path.name,
                )
            else:
                extracted_text = extract_image_text(file_path, config)
                increment_daily_counter(state_dir)

    try:
        return create_extracted_stub(
            vault_path=vault_path,
            source_filename=file_path.name,
            extracted_text=extracted_text,
            media_type=media_type,
            msg_id=msg_id,
        )
    except Exception:
        logger.warning("Failed to create extraction stub for %s", file_path.name, exc_info=True)
        return None
