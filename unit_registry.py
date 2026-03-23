"""Unit registry loaded from OpenRA RA rules YAML files."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import re
from threading import RLock
from typing import Iterable, Optional

_CAMEL_BOUNDARY = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
_SPACE_RUN = re.compile(r"\s+")
_HAS_CJK = re.compile(r"[\u4e00-\u9fff]")

_ROOT = Path(__file__).resolve().parent
_DEFAULT_RULES_DIR = _ROOT / "OpenCodeAlert" / "mods" / "ra" / "rules"
_DEFAULT_COPILOT_PATH = _ROOT / "OpenCodeAlert" / "mods" / "common" / "Copilot.yaml"
_RULE_FILES = {
    "structures": ("structures.yaml", "building"),
    "vehicles": ("vehicles.yaml", "vehicle"),
    "infantry": ("infantry.yaml", "infantry"),
    "aircraft": ("aircraft.yaml", "aircraft"),
    "ships": ("ships.yaml", "ship"),
}


def normalize_registry_name(text: str | None) -> str:
    raw = (text or "").strip()
    if not raw:
        return ""
    if raw.isascii():
        raw = raw.replace("_", " ").replace("-", " ")
        raw = _CAMEL_BOUNDARY.sub(" ", raw)
        raw = _SPACE_RUN.sub(" ", raw).strip().lower()
    return raw


@dataclass(frozen=True, slots=True)
class UnitEntry:
    unit_id: str
    display_name: str
    category: str
    queue_type: str
    cost: int
    faction: str
    prerequisites: list[str] = field(default_factory=list)
    aliases: list[str] = field(default_factory=list)


class UnitRegistry:
    """Central unit/building metadata registry."""

    def __init__(self, entries: Iterable[UnitEntry]) -> None:
        self._entries: list[UnitEntry] = list(entries)
        self._by_id: dict[str, UnitEntry] = {entry.unit_id.upper(): entry for entry in self._entries}
        self._alias_to_ids: dict[str, list[str]] = {}
        self._queue_index: dict[str, list[UnitEntry]] = {}
        for entry in self._entries:
            self._queue_index.setdefault(entry.queue_type.lower(), []).append(entry)
            candidates = [entry.unit_id, entry.unit_id.lower(), entry.display_name, *entry.aliases]
            seen: set[str] = set()
            for alias in candidates:
                normalized = normalize_registry_name(alias)
                if not normalized or normalized in seen:
                    continue
                seen.add(normalized)
                self._alias_to_ids.setdefault(normalized, []).append(entry.unit_id.upper())

    @classmethod
    def load(
        cls,
        *,
        rules_dir: Path | str = _DEFAULT_RULES_DIR,
        copilot_path: Path | str = _DEFAULT_COPILOT_PATH,
    ) -> "UnitRegistry":
        rules_root = Path(rules_dir)
        alias_groups = _load_alias_groups(Path(copilot_path))
        entries: list[UnitEntry] = []
        for _, (filename, default_category) in _RULE_FILES.items():
            path = rules_root / filename
            if not path.exists():
                continue
            data = _load_rules_yaml(path)
            for unit_id, traits in data.items():
                buildable = traits.get("Buildable")
                if not isinstance(buildable, dict):
                    continue
                queue_type = str(buildable.get("Queue") or "").strip()
                if not queue_type:
                    continue
                entry = _build_entry(
                    unit_id=str(unit_id),
                    traits=traits,
                    default_category=default_category,
                    queue_type=queue_type,
                    alias_groups=alias_groups,
                )
                entries.append(entry)
        return cls(entries)

    def get(self, unit_id: str | None) -> Optional[UnitEntry]:
        if not unit_id:
            return None
        return self._by_id.get(str(unit_id).upper())

    def resolve_name(self, text: str | None) -> Optional[UnitEntry]:
        matches = self.find_matches(text)
        return matches[0] if matches else None

    def find_matches(self, text: str | None) -> list[UnitEntry]:
        normalized = normalize_registry_name(text)
        if not normalized:
            return []
        ids = self._alias_to_ids.get(normalized)
        if ids:
            return [self._by_id[unit_id] for unit_id in ids if unit_id in self._by_id]
        return []

    def list_buildable(self, queue_type: str, faction: str | None = None) -> list[UnitEntry]:
        requested_queue = str(queue_type or "").strip().lower()
        requested_faction = str(faction or "any").strip().lower()
        entries = list(self._queue_index.get(requested_queue, []))
        if requested_faction in {"", "any"}:
            return entries
        return [
            entry
            for entry in entries
            if entry.faction == "any" or entry.faction == requested_faction
        ]

    def match_in_text(
        self,
        text: str | None,
        *,
        queue_types: Optional[Iterable[str]] = None,
    ) -> Optional[UnitEntry]:
        normalized_text = normalize_registry_name(text)
        if not normalized_text:
            return None
        allowed = None
        if queue_types is not None:
            allowed = {str(queue).lower() for queue in queue_types}
        best: tuple[int, int, Optional[UnitEntry]] = (-1, 0, None)
        for index, entry in enumerate(self._entries):
            if allowed is not None and entry.queue_type.lower() not in allowed:
                continue
            for alias in [entry.display_name, entry.unit_id, entry.unit_id.lower(), *entry.aliases]:
                normalized_alias = normalize_registry_name(alias)
                if normalized_alias and normalized_alias in normalized_text:
                    score = (len(normalized_alias), -index)
                    if score > (best[0], best[1]):
                        best = (score[0], score[1], entry)
        return best[2]

    def entries(self) -> list[UnitEntry]:
        return list(self._entries)


def _load_rules_yaml(path: Path) -> dict:
    loaded: dict[str, dict[str, object]] = {}
    current_unit: Optional[str] = None
    current_section: Optional[str] = None
    for raw_line in path.read_text(encoding="utf-8").replace("\t", "    ").splitlines():
        line = raw_line.rstrip()
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        indent = len(line) - len(line.lstrip(" "))
        if indent == 0 and stripped.endswith(":"):
            current_unit = stripped[:-1]
            current_section = None
            loaded[current_unit] = {}
            continue
        if current_unit is None:
            continue
        if indent == 4 and stripped.endswith(":"):
            current_section = stripped[:-1]
            loaded[current_unit].setdefault(current_section, {})
            continue
        if indent == 8 and ":" in stripped and current_section is not None:
            key, value = stripped.split(":", 1)
            section = loaded[current_unit].setdefault(current_section, {})
            if isinstance(section, dict):
                section[key.strip()] = value.strip()
    return loaded


def _load_alias_groups(path: Path) -> dict[str, list[str]]:
    groups: dict[str, list[str]] = {}
    if not path.exists():
        return groups

    current_key: Optional[str] = None
    in_units = False
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        stripped = raw_line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if not raw_line.startswith(" "):
            in_units = stripped == "units:"
            current_key = None
            continue
        if not in_units:
            continue
        if raw_line.startswith("    ") and stripped.endswith(":") and not raw_line.startswith("        "):
            current_key = stripped[:-1].strip().lower()
            groups.setdefault(current_key, [])
            continue
        if current_key and raw_line.startswith("        "):
            groups[current_key].append(stripped)
    return groups


def _build_entry(
    *,
    unit_id: str,
    traits: dict,
    default_category: str,
    queue_type: str,
    alias_groups: dict[str, list[str]],
) -> UnitEntry:
    valued = traits.get("Valued") if isinstance(traits.get("Valued"), dict) else {}
    buildable = traits.get("Buildable") if isinstance(traits.get("Buildable"), dict) else {}
    aliases = list(dict.fromkeys(alias_groups.get(unit_id.lower(), [])))
    display_name = _pick_display_name(unit_id, aliases)
    return UnitEntry(
        unit_id=unit_id.upper(),
        display_name=display_name,
        category=_infer_category(default_category, queue_type),
        queue_type=queue_type,
        cost=int(valued.get("Cost") or 0),
        faction=_infer_faction(buildable.get("Prerequisites")),
        prerequisites=_normalize_prerequisites(buildable.get("Prerequisites")),
        aliases=aliases,
    )


def _pick_display_name(unit_id: str, aliases: list[str]) -> str:
    for alias in aliases:
        if _HAS_CJK.search(alias):
            return alias
    for alias in aliases:
        if alias.isascii():
            return alias
    return unit_id.upper()


def _infer_category(default_category: str, queue_type: str) -> str:
    queue = queue_type.lower()
    if queue == "defense":
        return "defense"
    if queue == "building":
        return "building"
    if queue == "infantry":
        return "infantry"
    if queue == "vehicle":
        return "vehicle"
    if queue == "aircraft":
        return "aircraft"
    if queue == "ship":
        return "ship"
    return default_category


def _infer_faction(prerequisites: object) -> str:
    values = _normalize_prerequisites(prerequisites)
    joined = " ".join(values).lower()
    if "allies" in joined:
        return "allies"
    if "soviet" in joined:
        return "soviet"
    return "any"


def _normalize_prerequisites(value: object) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return [str(value).strip()]


_DEFAULT_REGISTRY: Optional[UnitRegistry] = None
_LOCK = RLock()


def get_default_registry() -> UnitRegistry:
    global _DEFAULT_REGISTRY
    with _LOCK:
        if _DEFAULT_REGISTRY is None:
            _DEFAULT_REGISTRY = UnitRegistry.load()
        return _DEFAULT_REGISTRY


def set_default_registry(registry: UnitRegistry) -> None:
    global _DEFAULT_REGISTRY
    with _LOCK:
        _DEFAULT_REGISTRY = registry
