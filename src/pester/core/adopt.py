"""Vault adoption — scan, detect, score, and onboard existing vaults."""

from __future__ import annotations

import os
import random
import re
import subprocess
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from pester.core.config import DEFAULT_CONFIG, _deep_merge
from pester.core.vault import _SKIP_DIRS, atomic_write, parse_frontmatter

# ── Folder alias table ───────────────────────────────────────────────────────

FOLDER_ALIASES: dict[str, list[str]] = {
    "journal": ["journal", "daily", "daily-notes", "diary", "dailies", "logs"],
    "meetings": ["meetings", "meeting-notes", "calls", "syncs", "meeting"],
    "decisions": ["decisions", "adrs", "adr", "decision-log", "decision-records"],
    "people": ["people", "contacts", "team", "stakeholders", "network", "persons"],
    "projects": ["projects", "initiatives", "workstreams", "epics"],
    "reference": [
        "reference",
        "resources",
        "docs",
        "library",
        "attachments",
        "kb",
        "notes",
        "inbox",
    ],
}

# Build reverse lookup: alias -> pester_type
_ALIAS_TO_TYPE: dict[str, str] = {}
for _type, _aliases in FOLDER_ALIASES.items():
    for _alias in _aliases:
        _ALIAS_TO_TYPE[_alias] = _type

# ── Tooling detection ────────────────────────────────────────────────────────

TOOLING_PATTERNS: list[tuple[str, str, str]] = [
    ("vault-mcp.py", "mcp", "Custom MCP server script"),
    (".mcp.json", "mcp", "MCP configuration"),
    (".obsidian", "obsidian", "Obsidian configuration"),
    ("scripts", "script", "Automation scripts"),
    (".vault-index", "rag", "Vault search index"),
    ("vault_rag", "rag", "Custom RAG implementation"),
    ("CLAUDE.md", "config", "Claude AI instructions"),
    ("AGENTS.md", "config", "Agent instructions"),
    (".env", "config", "Environment configuration"),
]

# Dirs to skip during scanning (superset of vault.py's _SKIP_DIRS)
_SCAN_SKIP = _SKIP_DIRS | {".obsidian", ".conductor", ".claude", ".context"}


# ── Data classes ─────────────────────────────────────────────────────────────


@dataclass
class FolderInfo:
    """Information about a single folder in the vault."""

    name: str
    path: Path
    md_count: int = 0
    frontmatter_types: Counter = field(default_factory=Counter)
    sample_titles: list[str] = field(default_factory=list)


@dataclass
class FolderMapping:
    """Mapping from an existing folder to an pester type."""

    folder: FolderInfo
    pester_type: str | None = None
    confidence: float = 0.0
    reason: str = ""


@dataclass
class ToolingInfo:
    """Detected existing tooling in the vault."""

    name: str
    path: Path
    kind: str
    description: str = ""


@dataclass
class VaultScan:
    """Complete scan result for an existing vault."""

    root: Path
    folders: list[FolderInfo] = field(default_factory=list)
    total_md_files: int = 0
    frontmatter_coverage: float = 0.0
    detected_language: str = "en"
    detected_owner: str | None = None
    existing_tooling: list[ToolingInfo] = field(default_factory=list)
    has_pester_yaml: bool = False
    existing_config: dict | None = None
    has_wikilinks: bool = False
    date_named_ratio: float = 0.0


@dataclass
class CompatibilityReport:
    """Compatibility score with breakdown."""

    overall_score: int = 0
    factors: list[dict] = field(default_factory=list)


@dataclass
class PlannedFile:
    """A file that will be created or modified during adoption."""

    path: Path
    content: str
    action: str  # "create" or "overwrite"
    description: str = ""


@dataclass
class AdoptPlan:
    """Everything that will happen during adoption."""

    dirs_to_create: list[Path] = field(default_factory=list)
    files_to_create: list[PlannedFile] = field(default_factory=list)
    config: dict = field(default_factory=dict)
    folder_mappings: list[FolderMapping] = field(default_factory=list)
    compatibility: CompatibilityReport = field(default_factory=CompatibilityReport)
    scan: VaultScan | None = None


# ── Core functions ───────────────────────────────────────────────────────────


def validate_adopt_target(path: Path) -> None:
    """Validate that path is suitable for adoption.

    Raises click.ClickException on failure.
    """
    import click

    if not path.exists():
        raise click.ClickException(f"Path does not exist: {path}")
    if path.is_file():
        raise click.ClickException(f"Path is a file, not a directory: {path}")
    if not os.access(path, os.W_OK):
        raise click.ClickException(f"Path is not writable: {path}")


def _collect_md_files(folder: Path) -> list[Path]:
    """Collect .md files in a folder (non-recursive, fast)."""
    try:
        return sorted(p for p in folder.iterdir() if p.suffix == ".md" and p.is_file())
    except PermissionError:
        return []


def _sample_frontmatter(
    md_files: list[Path], max_samples: int = 50
) -> tuple[Counter, int, list[str]]:
    """Sample frontmatter from md files. Returns (type_counter, with_fm_count, titles)."""
    types: Counter = Counter()
    with_fm = 0
    titles: list[str] = []

    sample = (
        md_files[:max_samples]
        if len(md_files) <= max_samples
        else random.sample(md_files, max_samples)
    )

    for p in sample:
        fm = parse_frontmatter(p)
        if fm is not None:
            with_fm += 1
            t = fm.get("type")
            if t:
                types[str(t)] += 1
        if len(titles) < 3:
            titles.append(p.stem)

    return types, with_fm, titles


def scan_vault(vault_path: Path) -> VaultScan:
    """Scan an existing vault and collect metadata."""
    scan = VaultScan(root=vault_path)

    # Check for pester.yaml
    config_path = vault_path / "pester.yaml"
    if config_path.is_file():
        scan.has_pester_yaml = True
        try:
            raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                scan.existing_config = raw
        except (yaml.YAMLError, OSError):
            pass

    # Scan top-level directories
    all_md_files: list[Path] = []
    try:
        entries = sorted(vault_path.iterdir())
    except PermissionError:
        return scan

    for entry in entries:
        if not entry.is_dir():
            continue
        name = entry.name
        if name.startswith(".") or name in _SCAN_SKIP:
            continue
        if name.startswith("_"):
            continue  # system dirs like _system

        md_files = _collect_md_files(entry)
        # Also check one level deeper
        try:
            for sub in sorted(entry.iterdir()):
                if sub.is_dir() and not sub.name.startswith("."):
                    md_files.extend(_collect_md_files(sub))
        except PermissionError:
            pass

        types, _, titles = _sample_frontmatter(md_files)
        folder = FolderInfo(
            name=name,
            path=entry,
            md_count=len(md_files),
            frontmatter_types=types,
            sample_titles=titles,
        )
        scan.folders.append(folder)
        all_md_files.extend(md_files)

    # Also count top-level .md files
    top_md = _collect_md_files(vault_path)
    all_md_files.extend(top_md)

    scan.total_md_files = len(all_md_files)

    # Frontmatter coverage across all files
    if all_md_files:
        sample = (
            all_md_files[:100] if len(all_md_files) <= 100 else random.sample(all_md_files, 100)
        )
        with_fm = sum(1 for p in sample if parse_frontmatter(p) is not None)
        scan.frontmatter_coverage = with_fm / len(sample)

    # Detect language, owner, tooling
    scan.detected_language = detect_language(vault_path, all_md_files)
    scan.detected_owner = detect_owner(vault_path)
    scan.existing_tooling = detect_tooling(vault_path)

    # Check for wikilinks and date naming
    if all_md_files:
        wikilink_re = re.compile(r"\[\[.+?\]\]")
        date_re = re.compile(r"^\d{4}-\d{2}-\d{2}")
        sample = all_md_files[:30] if len(all_md_files) <= 30 else random.sample(all_md_files, 30)
        wl_count = 0
        date_count = 0
        for p in sample:
            try:
                text = p.read_text(encoding="utf-8")[:2000]
                if wikilink_re.search(text):
                    wl_count += 1
            except OSError:
                pass
            if date_re.match(p.stem):
                date_count += 1
        scan.has_wikilinks = wl_count > 0
        scan.date_named_ratio = date_count / len(sample) if sample else 0.0

    return scan


def detect_language(vault_path: Path, md_files: list[Path], sample_size: int = 20) -> str:
    """Detect primary language by scanning for Cyrillic vs Latin script."""
    if not md_files:
        return "en"

    sample = (
        md_files[:sample_size]
        if len(md_files) <= sample_size
        else random.sample(md_files, sample_size)
    )
    cyrillic = 0
    latin = 0

    for p in sample:
        try:
            text = p.read_text(encoding="utf-8")[:1000]
        except OSError:
            continue
        # Skip frontmatter
        if text.startswith("---"):
            end = text.find("---", 3)
            if end != -1:
                text = text[end + 3 :]

        for ch in text:
            if "\u0400" <= ch <= "\u04ff":
                cyrillic += 1
            elif "A" <= ch <= "Z" or "a" <= ch <= "z":
                latin += 1

    total = cyrillic + latin
    if total == 0:
        return "en"
    if cyrillic / total > 0.7:
        return "ru"
    if latin / total > 0.7:
        return "en"
    return "mixed"


def detect_owner(vault_path: Path) -> str | None:
    """Try to detect vault owner from git config."""
    try:
        result = subprocess.run(
            ["git", "config", "--local", "user.name"],
            capture_output=True,
            text=True,
            cwd=vault_path,
            timeout=5,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
        # Fall back to global config if in a git repo
        check = subprocess.run(
            ["git", "rev-parse", "--git-dir"],
            capture_output=True,
            cwd=vault_path,
            timeout=5,
        )
        if check.returncode == 0:
            result = subprocess.run(
                ["git", "config", "user.name"],
                capture_output=True,
                text=True,
                cwd=vault_path,
                timeout=5,
            )
            if result.returncode == 0 and result.stdout.strip():
                return result.stdout.strip()
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        pass
    return None


def detect_tooling(vault_path: Path) -> list[ToolingInfo]:
    """Find existing tooling files in the vault."""
    found: list[ToolingInfo] = []
    for pattern, kind, desc in TOOLING_PATTERNS:
        target = vault_path / pattern
        if target.exists():
            found.append(ToolingInfo(name=pattern, path=target, kind=kind, description=desc))
    return found


def compute_folder_map(folders: list[FolderInfo]) -> list[FolderMapping]:
    """Auto-detect pester type for each folder using alias + frontmatter heuristics."""
    mappings: list[FolderMapping] = []
    used_types: set[str] = set()

    # Score each folder
    scored: list[tuple[FolderInfo, str, float, str]] = []

    for folder in folders:
        name_lower = folder.name.lower()

        best_type = None
        best_score = 0.0
        best_reason = ""

        # Check alias table
        if name_lower in _ALIAS_TO_TYPE:
            candidate = _ALIAS_TO_TYPE[name_lower]
            is_exact = name_lower == candidate
            score = 1.0 if is_exact else 0.8
            reason = "exact name match" if is_exact else f"alias match ({name_lower})"
            if score > best_score:
                best_type, best_score, best_reason = candidate, score, reason

        # Check frontmatter type majority
        if folder.frontmatter_types and folder.md_count > 0:
            top_type, top_count = folder.frontmatter_types.most_common(1)[0]
            sampled = min(folder.md_count, 50)
            if sampled > 0 and top_count / sampled > 0.5:
                if top_type in FOLDER_ALIASES and 0.6 > best_score:
                    best_type = top_type
                    best_score = 0.6
                    best_reason = f"frontmatter type={top_type} ({top_count}/{sampled})"

        if best_type and best_score >= 0.3:
            scored.append((folder, best_type, best_score, best_reason))
        else:
            mappings.append(FolderMapping(folder=folder))

    # Assign types, highest score first, avoiding duplicates
    scored.sort(key=lambda x: -x[2])
    for folder, pester_type, confidence, reason in scored:
        if pester_type not in used_types:
            used_types.add(pester_type)
            mappings.append(
                FolderMapping(
                    folder=folder,
                    pester_type=pester_type,
                    confidence=confidence,
                    reason=reason,
                )
            )
        else:
            mappings.append(FolderMapping(folder=folder))

    # Sort by folder name for stable output
    mappings.sort(key=lambda m: m.folder.name)
    return mappings


def compute_compatibility(scan: VaultScan, mappings: list[FolderMapping]) -> CompatibilityReport:
    """Compute a 0-100 compatibility score."""
    factors: list[dict] = []

    # Factor 1: Folder mapping coverage (30 points)
    mapped = sum(1 for m in mappings if m.pester_type is not None)
    total = len(mappings) or 1
    score1 = int(30 * min(1.0, mapped / min(total, 6)))
    factors.append(
        {
            "name": "Folder mapping",
            "score": score1,
            "weight": 30,
            "detail": f"{mapped}/{total} folders mapped",
        }
    )

    # Factor 2: Frontmatter coverage (25 points)
    score2 = int(25 * scan.frontmatter_coverage)
    pct = int(scan.frontmatter_coverage * 100)
    factors.append(
        {
            "name": "Frontmatter",
            "score": score2,
            "weight": 25,
            "detail": f"{pct}% of files have frontmatter",
        }
    )

    # Factor 3: Type field presence (15 points)
    type_count = sum(sum(f.frontmatter_types.values()) for f in scan.folders)
    fm_count = int(scan.frontmatter_coverage * scan.total_md_files) or 1
    type_ratio = min(1.0, type_count / fm_count) if fm_count > 0 else 0
    score3 = int(15 * type_ratio)
    factors.append(
        {
            "name": "Type field",
            "score": score3,
            "weight": 15,
            "detail": f"{int(type_ratio * 100)}% have type field",
        }
    )

    # Factor 4: Wikilinks (10 points)
    score4 = 10 if scan.has_wikilinks else 0
    factors.append(
        {
            "name": "Wikilinks",
            "score": score4,
            "weight": 10,
            "detail": "found" if scan.has_wikilinks else "not found",
        }
    )

    # Factor 5: Date naming convention (10 points)
    score5 = int(10 * scan.date_named_ratio)
    factors.append(
        {
            "name": "Date naming",
            "score": score5,
            "weight": 10,
            "detail": f"{int(scan.date_named_ratio * 100)}% YYYY-MM-DD",
        }
    )

    # Factor 6: Standard structure (10 points)
    exact = sum(1 for m in mappings if m.confidence >= 1.0)
    score6 = int(10 * min(1.0, exact / 4))
    factors.append(
        {"name": "Standard dirs", "score": score6, "weight": 10, "detail": f"{exact} exact matches"}
    )

    overall = sum(f["score"] for f in factors)
    return CompatibilityReport(overall_score=overall, factors=factors)


def build_config(scan: VaultScan, mappings: list[FolderMapping]) -> dict:
    """Assemble an pester.yaml config dict from scan results."""
    vault_name = scan.root.name.replace("-", " ").replace("_", " ").title()

    overrides: dict = {
        "vault": {
            "name": vault_name,
            "language": scan.detected_language,
            "owner": scan.detected_owner or "",
        },
    }

    # Add bilingual keywords if mixed language
    if scan.detected_language in ("ru", "mixed"):
        overrides["extraction"] = {
            "keywords": {
                "en": DEFAULT_CONFIG["extraction"]["keywords"]["en"],
                "ru": DEFAULT_CONFIG["extraction"]["keywords"]["ru"],
            },
        }

    # Build folder_map for non-standard names
    folder_map: dict[str, str] = {}
    for m in mappings:
        if m.pester_type and m.folder.name != m.pester_type:
            folder_map[m.pester_type] = m.folder.name
    if folder_map:
        overrides["folder_map"] = folder_map

    return _deep_merge(DEFAULT_CONFIG, overrides)


def build_adopt_plan(
    vault_path: Path,
    scan: VaultScan,
    mappings: list[FolderMapping],
    compatibility: CompatibilityReport,
    force: bool = False,
) -> AdoptPlan:
    """Build the full adoption plan."""
    from pester.cli.cmd_init import _get_template_root, _target_name

    plan = AdoptPlan(
        folder_mappings=mappings,
        compatibility=compatibility,
        scan=scan,
    )

    config = build_config(scan, mappings)
    plan.config = config

    # pester.yaml
    if not scan.has_pester_yaml or force:
        config_content = "# pester.yaml — pester Vault Configuration\n\n"
        config_content += yaml.dump(
            config, default_flow_style=False, allow_unicode=True, sort_keys=False
        )
        action = "overwrite" if scan.has_pester_yaml else "create"
        plan.files_to_create.append(
            PlannedFile(
                path=vault_path / "pester.yaml",
                content=config_content,
                action=action,
                description="Vault configuration",
            )
        )

    # actions/ directory
    actions_dir = vault_path / "actions"
    if not actions_dir.exists():
        plan.dirs_to_create.append(actions_dir)
        plan.files_to_create.append(
            PlannedFile(
                path=actions_dir / ".gitkeep",
                content="",
                action="create",
                description="Actions directory placeholder",
            )
        )

    # _system/templates/ — copy from package templates
    templates_dir = vault_path / "_system" / "templates"
    if not templates_dir.exists():
        plan.dirs_to_create.append(templates_dir)
        template_root = _get_template_root()
        system_templates = template_root / "_system" / "templates"
        try:
            for item in sorted(system_templates.iterdir(), key=lambda x: x.name):
                if item.is_file() and item.name != "__init__.py":
                    dest_name = _target_name(item.name)
                    plan.files_to_create.append(
                        PlannedFile(
                            path=templates_dir / dest_name,
                            content=item.read_text(encoding="utf-8"),
                            action="create",
                            description=f"Template: {dest_name}",
                        )
                    )
        except (OSError, TypeError):
            pass

    # _system/prompts/ — copy coaching prompt templates (locale subdirs en/, ru/)
    prompts_dir = vault_path / "_system" / "prompts"
    if not prompts_dir.exists():
        plan.dirs_to_create.append(prompts_dir)
        template_root = _get_template_root()
        system_prompts = template_root / "_system" / "prompts"
        try:
            for item in sorted(system_prompts.rglob("*.md")):
                if not item.is_file():
                    continue
                rel = item.relative_to(system_prompts)
                dest = prompts_dir / rel
                if dest.parent != prompts_dir and dest.parent not in plan.dirs_to_create:
                    plan.dirs_to_create.append(dest.parent)
                plan.files_to_create.append(
                    PlannedFile(
                        path=dest,
                        content=item.read_text(encoding="utf-8"),
                        action="create",
                        description=f"Prompt: {rel.as_posix()}",
                    )
                )
        except (OSError, TypeError):
            pass

    # .gitignore — merge if exists, create if not
    gitignore_path = vault_path / ".gitignore"
    template_root = _get_template_root()
    try:
        template_gi = (template_root / ".gitignore").read_text(encoding="utf-8")
    except (OSError, TypeError):
        template_gi = ""

    if gitignore_path.is_file() and template_gi:
        existing_gi = gitignore_path.read_text(encoding="utf-8")
        merged = _merge_gitignore(existing_gi, template_gi)
        if merged != existing_gi:
            plan.files_to_create.append(
                PlannedFile(
                    path=gitignore_path,
                    content=merged,
                    action="overwrite",
                    description=".gitignore (merged)",
                )
            )
    elif not gitignore_path.exists() and template_gi:
        plan.files_to_create.append(
            PlannedFile(
                path=gitignore_path,
                content=template_gi,
                action="create",
                description=".gitignore",
            )
        )

    return plan


def _merge_gitignore(existing: str, template: str) -> str:
    """Add missing entries from template to existing .gitignore."""
    existing_lines = set(existing.strip().splitlines())
    new_lines = [
        line
        for line in template.strip().splitlines()
        if line not in existing_lines and line.strip() and not line.startswith("#")
    ]
    if not new_lines:
        return existing
    return existing.rstrip() + "\n\n# Added by pester adopt\n" + "\n".join(new_lines) + "\n"


def execute_adopt(vault_path: Path, plan: AdoptPlan) -> list[Path]:
    """Execute the adoption plan. Returns list of created/modified paths."""
    created: list[Path] = []

    for d in plan.dirs_to_create:
        d.mkdir(parents=True, exist_ok=True)

    for f in plan.files_to_create:
        atomic_write(f.path, f.content)
        created.append(f.path)

    return created
