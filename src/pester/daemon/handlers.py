"""Event handlers that subscribe to bus events."""

from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Any

from pester.core.audit import log_event
from pester.daemon.events import ComponentEvent

logger = logging.getLogger(__name__)

_index_lock = threading.Lock()


def handle_file_changed_extract(payload: dict, vault_path: Path, config: dict[str, Any]) -> None:
    """Extract actions from meeting files when auto_extract is enabled.

    Called on FILE_CHANGED events.  Checks whether the changed file
    resides in one of the configured auto-extract directories and, if
    so, runs the extractor and emits ACTIONS_EXTRACTED.
    """
    watcher_cfg = config.get("watcher", {})
    auto_extract = watcher_cfg.get("auto_extract", {})
    if not auto_extract.get("enabled", False):
        return

    file_path: Path = Path(payload["path"])
    vault: Path = Path(payload["vault"])
    change_type: str = payload.get("change_type", "modified")

    if change_type == "deleted":
        return

    # Check if the file is in one of the auto-extract directories
    try:
        rel = file_path.relative_to(vault)
    except ValueError:
        return

    directories = auto_extract.get("directories", ["meetings"])
    in_extract_dir = any(rel.parts[0] == d for d in directories) if rel.parts else False
    if not in_extract_dir:
        return

    try:
        from pester.tracking.extractor import extract_from_meeting

        # Try LLM extractor first (if available), then regex
        llm_actions: list[dict] = []
        try:
            from pester.tracking.llm_extractor import extract_with_llm

            llm_actions = extract_with_llm(file_path.read_text(encoding="utf-8"), config)
        except Exception:
            logger.debug("LLM extraction unavailable or failed for %s", rel, exc_info=True)

        regex_actions = extract_from_meeting(file_path, config)

        # Dedupe results between the two
        if llm_actions:
            from pester.tracking.llm_extractor import dedupe_actions

            candidates = dedupe_actions(llm_actions, regex_actions)
        else:
            candidates = regex_actions

        # Drop candidates that already exist as actions in the vault. Without
        # this, every re-edit of a meeting file re-creates duplicate actions
        # for items the user already saw or accepted.
        if candidates:
            from pester.tracking.actions import list_actions
            from pester.tracking.llm_extractor import filter_existing_actions

            existing = list_actions(vault)
            candidates = filter_existing_actions(candidates, existing)

        action_count = len(candidates)
        logger.info("Extracted %d action candidate(s) from %s", action_count, rel)

        # Auto-create high-confidence candidates; queue lower-confidence ones for review.
        auto_create_threshold = float(auto_extract.get("auto_create_confidence", 0.95))
        auto_created: list[str] = []
        needs_review: list[dict[str, Any]] = []
        if candidates:
            from pester.tracking.actions import create_action

            for c in candidates:
                if c.get("confidence", 0.0) >= auto_create_threshold and c.get("owner"):
                    try:
                        slug = create_action(
                            vault_path=vault,
                            description=c["desc"],
                            owner=c["owner"],
                            due=c.get("due") or "",
                            source=f"daemon:{rel}",
                        )
                        auto_created.append(slug)
                        logger.info("Auto-created action: %s (from %s)", slug, rel)
                    except Exception:
                        logger.warning(
                            "Auto-create failed for candidate from %s", rel, exc_info=True
                        )
                        needs_review.append(c)
                else:
                    needs_review.append(c)

        # Import bus late — the handler is typically called via the bus itself
        # but we need to emit the follow-up event.
        from pester.daemon.bus import EventBus

        # We get the bus from the payload if provided, otherwise skip
        bus: EventBus | None = payload.get("_bus")
        if bus is not None:
            bus.emit(
                ComponentEvent.ACTIONS_EXTRACTED,
                {
                    "source_path": file_path,
                    "vault": vault,
                    "action_count": action_count,
                    "auto_created": auto_created,
                    "needs_review": needs_review,
                },
            )
    except Exception:
        logger.warning("Extraction failed for %s", rel, exc_info=True)


def handle_file_changed_index(payload: dict, vault_path: Path, config: dict[str, Any]) -> None:
    """Re-index vault when a file changes (search extra required).

    Runs indexing in a single subprocess at a time to:
    - Isolate chromadb Rust FFI crashes (segfaults from worker threads)
    - Avoid OOM from parallel indexing subprocesses
    """
    from pester.rag import HAS_SEARCH

    if not HAS_SEARCH:
        return

    watcher_cfg = config.get("watcher", {})
    auto_index = watcher_cfg.get("auto_index", {})
    if not auto_index.get("enabled", False):
        return

    file_path: Path = Path(payload["path"])
    vault: Path = Path(payload["vault"])
    change_type: str = payload.get("change_type", "modified")

    if change_type == "deleted":
        return

    # Only one indexing subprocess at a time (skip if already running)
    if not _index_lock.acquire(blocking=False):
        logger.debug("Skipping index for %s — another index in progress", file_path)
        return

    try:
        import subprocess
        import sys

        from pester.core.config import get_config_value

        language = get_config_value(config, "vault.language", "en")
        score_factor = get_config_value(config, "search.transcript_score_factor", 0.85)
        provider = get_config_value(config, "search.provider", "e5")
        search_model = get_config_value(config, "search.model", "intfloat/multilingual-e5-base")
        ollama_url = get_config_value(config, "search.ollama_url", "http://localhost:11434")
        chunk_size_val = get_config_value(config, "search.chunk_size")

        result = subprocess.run(
            [
                sys.executable,
                "-c",
                "from pester.rag.indexer import VaultIndexer;"
                "from pester.rag.embeddings import create_embedder;"
                "from pester.core.state import ensure_state_dir;"
                "from pathlib import Path;"
                f"v=Path({str(vault)!r});"
                f"s=ensure_state_dir(v);"
                f"cfg={{'search':{{'provider':{provider!r},'model':{search_model!r},"
                f"'ollama_url':{ollama_url!r}}}}};"
                f"emb=create_embedder(cfg);"
                f"VaultIndexer(v,s,embedder=emb,language={language!r},"
                f"transcript_score_factor={score_factor!r},"
                f"chunk_size={chunk_size_val!r}).index_vault()",
            ],
            capture_output=True,
            text=True,
            timeout=300,
        )
        if result.returncode == 0:
            logger.info("Re-indexed vault after change to %s", file_path)
        else:
            logger.warning(
                "Auto-index subprocess exited %d for %s: %s",
                result.returncode,
                file_path,
                result.stderr[-500:] if result.stderr else "(no stderr)",
            )
    except Exception:
        logger.warning("Auto-index failed for %s", file_path, exc_info=True)
    finally:
        _index_lock.release()


def handle_audit(payload: dict, vault_path: Path, event_type: str) -> None:
    """Universal audit subscriber — logs every event to the JSONL audit trail."""
    try:
        # Convert Path objects to strings for JSON serialisation
        data = {}
        for key, value in payload.items():
            if key.startswith("_"):
                continue  # Skip internal keys
            if isinstance(value, Path):
                data[key] = str(value)
            else:
                data[key] = value

        log_event(vault_path, event_type, **data)
    except Exception:
        logger.warning("Audit logging failed for event %s", event_type, exc_info=True)
