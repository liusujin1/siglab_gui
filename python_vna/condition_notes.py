from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re


README_NAME = "readme.txt"
_CONDITION_LINE_RE = re.compile(
    r"^(?P<numbers>\d+(?:\s*[,，、]\s*\d+)*)\s*[:：]\s*(?P<text>.*)$"
)
_NUMBER_SPLIT_RE = re.compile(r"\s*[,，、]\s*")


@dataclass(slots=True)
class ConditionEntry:
    number: str
    label: str
    text: str
    line_index: int


def condition_number_from_path(path: str | Path | None) -> str | None:
    if path is None:
        return None
    stem = Path(path).stem
    match = re.search(r"(\d+)$", stem)
    return match.group(1) if match else None


def parse_condition_entries(text: str) -> dict[str, ConditionEntry]:
    entries: dict[str, ConditionEntry] = {}
    for index, line in enumerate(text.splitlines()):
        match = _CONDITION_LINE_RE.match(line.strip())
        if match is None:
            continue
        label = match.group("numbers").strip()
        numbers = [part.strip() for part in _NUMBER_SPLIT_RE.split(label) if part.strip()]
        for number in numbers:
            entries[number] = ConditionEntry(
                number=number,
                label=label,
                text=match.group("text").strip(),
                line_index=index,
            )
    return entries


def _entry_for_number(entries: dict[str, ConditionEntry], number: str | None) -> ConditionEntry | None:
    if not number:
        return None
    if number in entries:
        return entries[number]
    try:
        numeric_number = int(number)
    except ValueError:
        return None
    for entry_number, entry in entries.items():
        try:
            if int(entry_number) == numeric_number:
                return entry
        except ValueError:
            continue
    return None


def read_condition_readme(folder: str | Path) -> str:
    path = Path(folder) / README_NAME
    if not path.exists():
        return ""
    return read_condition_text_file(path)


def read_condition_text_file(path: str | Path) -> str:
    source = Path(path)
    if not source.exists():
        return ""
    data = source.read_bytes()
    for encoding in ("utf-8-sig", "utf-8", "gbk", "cp936"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def condition_for_number(readme_text: str, number: str | None) -> str | None:
    entry = _entry_for_number(parse_condition_entries(readme_text), number)
    return entry.text if entry is not None else None


def update_condition_readme_text(readme_text: str, number: str, condition: str) -> str:
    normalized_number = str(number).strip()
    if not normalized_number:
        return readme_text
    lines = readme_text.splitlines()
    entries = parse_condition_entries(readme_text)
    replacement = f"{normalized_number}：{condition.strip()}"
    existing_entry = _entry_for_number(entries, normalized_number)
    if existing_entry is not None:
        lines[existing_entry.line_index] = f"{existing_entry.label}：{condition.strip()}"
        return "\n".join(lines).rstrip() + "\n"

    insert_at = len(lines)
    try:
        current_value = int(normalized_number)
    except ValueError:
        current_value = None
    if current_value is not None:
        ordered_entries = sorted(entries.values(), key=lambda entry: entry.line_index)
        for entry in ordered_entries:
            try:
                entry_value = int(entry.number)
            except ValueError:
                continue
            if entry_value > current_value:
                insert_at = entry.line_index
                break
    lines.insert(insert_at, replacement)
    return "\n".join(lines).rstrip() + "\n"


def write_condition_readme(folder: str | Path, number: str, condition: str) -> Path:
    destination = Path(folder) / README_NAME
    destination.parent.mkdir(parents=True, exist_ok=True)
    existing = read_condition_readme(destination.parent)
    updated = update_condition_readme_text(existing, number, condition)
    destination.write_text(updated, encoding="utf-8-sig", newline="\n")
    return destination


def write_condition_readme_text(folder: str | Path, readme_text: str) -> Path:
    destination = Path(folder) / README_NAME
    destination.parent.mkdir(parents=True, exist_ok=True)
    text = readme_text.rstrip() + "\n" if readme_text else ""
    destination.write_text(text, encoding="utf-8-sig", newline="\n")
    return destination
