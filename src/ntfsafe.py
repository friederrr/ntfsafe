#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = [
#   "PySide6>=6.7,!=6.12.0,<7",
# ]
# ///
"""
NTFSafe — PySide6 edition

Scan a folder before copying it from Linux/ext4 to an NTFS drive. Select pure
NTFS, Windows, or macOS compatibility profiles. The scanner is read-only by
default: it never copies files or bulk-renames source files.

GUI dependency: PySide6. The command-line scanner does not require PySide6.
"""
from __future__ import annotations

import argparse
import csv
import json
import logging
import os
import platform
import shlex
import shutil
import subprocess
import sys
import threading
import time
import unicodedata
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable, Iterable, Optional, Sequence

APP_NAME = "NTFSafe"
APP_VERSION = "0.3.9"

WINDOWS_INVALID_CHARS = set('<>"\\|?*')
# Slash and NUL cannot occur inside a normal POSIX path component exposed by Python.
# Windows device names are reserved even with extensions. Windows also treats
# superscript 1/2/3 as digits in COM/LPT names.
RESERVED_BASENAMES = {
    "CON", "PRN", "AUX", "NUL", "CLOCK$", "CONIN$", "CONOUT$",
    *{f"COM{i}" for i in range(1, 10)},
    *{f"LPT{i}" for i in range(1, 10)},
    "COM¹", "COM²", "COM³", "LPT¹", "LPT²", "LPT³",
}

ISSUE_KIND_COPY_BLOCKER = "NTFS copy blocker"
ISSUE_KIND_WINDOWS_ACCESS = "Windows access risk"
ISSUE_KIND_MACOS_ACCESS = "macOS access risk"
ISSUE_KIND_PORTABILITY = "Portability warning"
ISSUE_KIND_EDITED = "Edited item"

PROFILE_NTFS = "ntfs"
PROFILE_WINDOWS = "windows"
PROFILE_MACOS = "macos"

PROFILE_LABELS = {
    PROFILE_NTFS: "Pure NTFS compatibility",
    PROFILE_WINDOWS: "Windows compatibility (NTFS + Windows rules)",
    PROFILE_MACOS: "macOS compatibility (NTFS + macOS/APFS-style rules)",
}

PROFILE_DESCRIPTIONS = {
    PROFILE_NTFS: "Checks only what an NTFS volume can represent: valid Unicode/POSIX-namespace name constraints, NTFS component/path limits, and ordinary special-file/link portability warnings.",
    PROFILE_WINDOWS: "Checks pure NTFS compatibility, then adds normal Windows filename, colon/ADS, collision, and legacy path-length risks.",
    PROFILE_MACOS: "Checks pure NTFS compatibility, then adds macOS/Finder-style colon, leading-dot, UTF-8 component/path length, and default case/normalization-collision risks.",
}

# GUI/CLI profile defaults. These are intentionally different because the
# profiles answer different questions:
# - pure NTFS: what can the volume format represent?
# - Windows: what will normal Windows tools tolerate on an NTFS volume?
# - macOS: what will macOS/Finder-style use tolerate on an NTFS volume?
PROFILE_SCAN_DEFAULTS = {
    PROFILE_NTFS: {
        "max_depth": 999,
        "max_file_path_utf16_units": 32760,
        "max_dir_path_utf16_units": 32760,
        "max_extended_path_utf16_units": 32760,
        "max_component_utf16_units": 255,
        "max_component_utf8_bytes": 1024,
        "max_macos_path_utf8_bytes": 32760,
        "max_macos_component_utf8_bytes": 1024,
        "long_paths_enabled": False,
        "check_crossrename_rules": False,
    },
    PROFILE_WINDOWS: {
        "max_depth": 12,
        "max_file_path_utf16_units": 259,
        "max_dir_path_utf16_units": 247,
        "max_extended_path_utf16_units": 32760,
        "max_component_utf16_units": 255,
        "max_component_utf8_bytes": 255,
        "max_macos_path_utf8_bytes": 1024,
        "max_macos_component_utf8_bytes": 255,
        "long_paths_enabled": False,
        "check_crossrename_rules": True,
    },
    PROFILE_MACOS: {
        "max_depth": 48,
        "max_file_path_utf16_units": 259,
        "max_dir_path_utf16_units": 247,
        "max_extended_path_utf16_units": 32760,
        "max_component_utf16_units": 255,
        "max_component_utf8_bytes": 255,
        "max_macos_path_utf8_bytes": 1024,
        "max_macos_component_utf8_bytes": 255,
        "long_paths_enabled": False,
        "check_crossrename_rules": True,
    },
}

ISSUE_ICONS = {
    ISSUE_KIND_COPY_BLOCKER: "⛔",
    ISSUE_KIND_WINDOWS_ACCESS: "🪟",
    ISSUE_KIND_MACOS_ACCESS: "🍎",
    ISSUE_KIND_PORTABILITY: "⚠️",
    ISSUE_KIND_EDITED: "✏️",
}

SEVERITY_ORDER = {"ERROR": 0, "WARNING": 1, "INFO": 2}
KIND_ORDER = {
    ISSUE_KIND_COPY_BLOCKER: 0,
    ISSUE_KIND_WINDOWS_ACCESS: 1,
    ISSUE_KIND_MACOS_ACCESS: 1,
    ISSUE_KIND_PORTABILITY: 2,
    ISSUE_KIND_EDITED: 3,
}


@dataclass(slots=True)
class Settings:
    compatibility_profile: str = PROFILE_WINDOWS
    dest_root: str = ""
    max_depth: int = 12
    warn_legacy_paths: bool = True
    max_file_path_utf16_units: int = 259   # MAX_PATH minus terminating NUL
    max_dir_path_utf16_units: int = 247    # conservative CreateDirectory-compatible warning
    max_extended_path_utf16_units: int = 32760
    max_component_utf16_units: int = 255   # NTFS/Win32 common component limit
    max_component_utf8_bytes: int = 255    # CrossRename/ext4-style byte-aware component check
    max_macos_path_utf8_bytes: int = 1024  # common macOS PATH_MAX-style practical check
    max_macos_component_utf8_bytes: int = 255
    check_nfc: bool = True
    check_casefold_collisions: bool = True
    warn_symlinks: bool = True
    warn_hardlinks: bool = True
    long_paths_enabled: bool = False
    check_crossrename_rules: bool = True


def settings_for_profile(profile: str) -> Settings:
    """Return Settings populated with the defaults for a compatibility profile."""
    profile = profile if profile in PROFILE_SCAN_DEFAULTS else PROFILE_WINDOWS
    settings = Settings(compatibility_profile=profile)
    for key, value in PROFILE_SCAN_DEFAULTS[profile].items():
        setattr(settings, key, value)
    return settings


@dataclass(slots=True)
class Issue:
    severity: str
    issue_kind: str
    icon: str
    object_type: str
    name: str
    location: str
    relative_path: str
    absolute_path: str
    problem: str
    detail: str
    check_id: str
    can_copy_to_ntfs: bool
    normal_windows_access_ok: bool


@dataclass(slots=True)
class EditedRecord:
    """One item renamed from the GUI during this session."""
    old_absolute_path: str
    new_absolute_path: str
    old_name: str
    new_name: str
    object_type: str
    location: str
    relative_path: str
    edited_at: str


def make_issue(
    *,
    severity: str,
    issue_kind: str,
    object_type: str,
    name: str,
    location: str,
    relative_path: str,
    absolute_path: str,
    problem: str,
    detail: str,
    check_id: str,
) -> Issue:
    return Issue(
        severity=severity,
        issue_kind=issue_kind,
        icon=ISSUE_ICONS.get(issue_kind, "⚠️"),
        object_type=object_type,
        name=name,
        location=location,
        relative_path=relative_path,
        absolute_path=absolute_path,
        problem=problem,
        detail=detail,
        check_id=check_id,
        can_copy_to_ntfs=issue_kind != ISSUE_KIND_COPY_BLOCKER,
        normal_windows_access_ok=issue_kind not in (ISSUE_KIND_WINDOWS_ACCESS, ISSUE_KIND_MACOS_ACCESS),
    )



@dataclass(slots=True)
class ProblemItem:
    """One table row representing one problematic or edited file/folder."""
    issues: list[Issue]
    primary_issue: Issue
    original_order: int
    edited: bool = False

    @property
    def problem_count(self) -> int:
        return len(self.issues)

    @property
    def is_multi_problem(self) -> bool:
        return len(self.issues) > 1


LENGTH_DEPTH_PATH_CHECK_IDS = {
    "ntfs.component_utf16_too_long",
    "portability.component_utf8_bytes_too_long",
    "macos.component_utf8_bytes_too_long",
    "path.depth_comfort_limit",
    "ntfs.extended_path_too_long",
    "windows.legacy_dir_path_too_long",
    "windows.legacy_file_path_too_long",
    "macos.path_utf8_bytes_too_long",
}


def issue_topic_sort_bucket(issue: Issue) -> int:
    """Order human-actionable name/content problems before path/depth/length problems.

    Multi-problem rows can contain both an intrinsic filename problem, such as an
    illegal Windows character, and a structural path problem, such as a long path
    or excessive nesting.  The filename/content problem is usually the first thing
    a user should see, because it identifies a concrete object to rename before
    considering larger path shortening.
    """
    return 1 if issue.check_id in LENGTH_DEPTH_PATH_CHECK_IDS else 0


def issue_sort_key(issue: Issue) -> tuple[int, int, int, str, str]:
    """Sort problems inside one row.

    Non-depth/non-length problems are listed first.  Path length, nesting-depth,
    and component-length problems are pushed later even when they are severe, so
    a row like ``bad?name/very-long-file`` shows the illegal-character problem
    before the filename/path-length problem.
    """
    return (
        issue_topic_sort_bucket(issue),
        KIND_ORDER.get(issue.issue_kind, 99),
        SEVERITY_ORDER.get(issue.severity, 99),
        issue.problem.casefold(),
        issue.check_id,
    )


def issue_item_key(issue: Issue) -> str:
    return issue.absolute_path


def group_problem_items(issues: Sequence[Issue], edited_paths: Optional[set[str]] = None) -> list[ProblemItem]:
    """Aggregate raw issue rows into one visible GUI row per filesystem item."""
    edited_paths = set(edited_paths or set())
    groups: dict[str, list[Issue]] = {}
    order: dict[str, int] = {}
    for idx, issue in enumerate(issues):
        key = issue_item_key(issue)
        groups.setdefault(key, []).append(issue)
        order.setdefault(key, idx)

    items: list[ProblemItem] = []
    for key, group in groups.items():
        ordered = sorted(group, key=issue_sort_key)
        is_edited = key in edited_paths or any(issue.check_id == "user.edited_item" for issue in ordered)
        items.append(ProblemItem(
            issues=ordered,
            primary_issue=ordered[0],
            original_order=order[key],
            edited=is_edited,
        ))
    items.sort(key=lambda item: item.original_order)
    return items


def _unique_preserve_order(values: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        if value not in seen:
            seen.add(value)
            out.append(value)
    return out


def problem_text_for_item(item: ProblemItem, *, multiline: bool = False) -> str:
    problems = _unique_preserve_order(issue.problem for issue in item.issues)
    return ("\n" if multiline else "; ").join(problems)


def detail_text_for_item(item: ProblemItem, *, multiline: bool = True) -> str:
    entries: list[str] = []
    seen: set[tuple[str, str]] = set()
    for issue in item.issues:
        key = (issue.problem, issue.detail)
        if key in seen:
            continue
        seen.add(key)
        if issue.detail:
            entries.append(f"• {issue.problem}: {issue.detail}")
        else:
            entries.append(f"• {issue.problem}")
    return ("\n" if multiline else "  |  ").join(entries)


def kind_text_for_item(item: ProblemItem) -> str:
    kinds = _unique_preserve_order(issue.issue_kind for issue in item.issues)
    if len(kinds) == 1:
        return kinds[0]
    return "Multiple: " + " + ".join(kinds)


def icon_text_for_item(item: ProblemItem) -> str:
    icons = _unique_preserve_order(issue.icon for issue in item.issues)
    if item.edited and "✏️" not in icons:
        icons.insert(0, "✏️")
    text = " ".join(icons)
    if item.is_multi_problem:
        return f"🟧 {text}"
    return text

def utf16_units(s: str) -> int:
    """Return Windows-ish UTF-16 code unit length, preserving surrogateescape chars."""
    return len(s.encode("utf-16-le", "surrogatepass")) // 2


def utf8_bytes(s: str) -> int:
    """Return UTF-8 byte length, preserving surrogateescape chars as raw-ish bytes."""
    return len(s.encode("utf-8", "surrogateescape"))


def contains_surrogateescape(s: str) -> bool:
    # Python decodes invalid filesystem bytes into U+DC80..U+DCFF using surrogateescape.
    return any(0xDC80 <= ord(ch) <= 0xDCFF for ch in s)


def safe_display(s: str) -> str:
    # Keep GUI/report printable even if a filename contains surrogateescape chars.
    return s.encode("utf-8", "backslashreplace").decode("utf-8")


def normalize_dest_root(dest_root: str) -> str:
    d = (dest_root or "").strip().strip('"').replace("/", "\\")
    if not d:
        return ""
    return d.rstrip("\\")


def windows_join(dest_root: str, rel_parts: Iterable[str]) -> str:
    parts = [p for p in rel_parts if p]
    root = normalize_dest_root(dest_root)
    if not parts:
        return root
    rel = "\\".join(parts)
    if not root:
        return rel
    return root + "\\" + rel


def posix_join(dest_root: str, rel_parts: Iterable[str]) -> str:
    parts = [p for p in rel_parts if p]
    root = (dest_root or "").strip().strip('"').replace("\\", "/").rstrip("/")
    if not parts:
        return root
    rel = "/".join(parts)
    if not root:
        return rel
    return root + "/" + rel


def profile_label(profile: str) -> str:
    return PROFILE_LABELS.get(profile, PROFILE_LABELS[PROFILE_WINDOWS])


def profile_os_issue_kind(profile: str) -> Optional[str]:
    if profile == PROFILE_WINDOWS:
        return ISSUE_KIND_WINDOWS_ACCESS
    if profile == PROFILE_MACOS:
        return ISSUE_KIND_MACOS_ACCESS
    return None


def profile_os_icon(profile: str) -> str:
    kind = profile_os_issue_kind(profile)
    return ISSUE_ICONS.get(kind, "💻") if kind else "💻"


def profile_os_label(profile: str) -> str:
    if profile == PROFILE_WINDOWS:
        return "Windows access risk"
    if profile == PROFILE_MACOS:
        return "macOS access risk"
    return "OS access risk"


def rel_display(rel_parts: Iterable[str]) -> str:
    parts = list(rel_parts)
    return "." if not parts else "/".join(safe_display(p) for p in parts)


def rel_raw(rel_parts: Iterable[str]) -> str:
    parts = list(rel_parts)
    return "." if not parts else "/".join(parts)


def windows_collision_key(name: str) -> str:
    # Conservative approximation, not a full Windows collation implementation.
    return unicodedata.normalize("NFC", name).casefold()


def is_windows_reserved_name(name: str) -> bool:
    # Windows device names are reserved even with extensions, e.g. CON.txt.
    # Windows also ignores trailing spaces/dots in many normal API contexts.
    trimmed = name.rstrip(" .")
    stem = trimmed.split(".", 1)[0]
    return stem.upper() in RESERVED_BASENAMES


def invalid_windows_chars_in(name: str) -> list[str]:
    bad: list[str] = []
    for ch in name:
        o = ord(ch)
        if ch in WINDOWS_INVALID_CHARS or o < 32:
            if ch not in bad:
                bad.append(ch)
    return bad


def format_bad_chars(chars: Sequence[str]) -> str:
    rendered = []
    for ch in chars:
        if ord(ch) < 32:
            rendered.append(f"U+{ord(ch):04X}")
        else:
            rendered.append(ch)
    return " ".join(rendered)


def ntfs_posix_namespace_forbidden_chars_in(name: str) -> list[str]:
    """Characters not representable in the most permissive NTFS/POSIX filename namespace.

    A normal Linux/ext4 source scan will never see '/' or NUL inside a single
    path component: '/' is the path separator and NUL cannot appear in POSIX
    path strings. Keeping this explicit check makes the pure-NTFS profile
    self-documenting and protects programmatic/test inputs.
    """
    bad: list[str] = []
    for ch in name:
        if ch == "\x00" or ch == "/":
            if ch not in bad:
                bad.append(ch)
    return bad


def absolute_from_rel(root_abs: str, rel_parts: list[str]) -> str:
    if not rel_parts:
        return root_abs
    return os.path.join(root_abs, *rel_parts)


def parent_location(root_abs: str, rel_parts: list[str]) -> str:
    parent_parts = rel_parts[:-1]
    if not parent_parts:
        return root_abs
    return os.path.join(root_abs, *parent_parts)


def analyze_component(
    *,
    root_abs: str,
    name: str,
    rel_parts: list[str],
    object_type: str,
    settings: Settings,
) -> list[Issue]:
    issues: list[Issue] = []
    rp_raw = rel_raw(rel_parts)
    abs_path = absolute_from_rel(root_abs, rel_parts)
    loc = parent_location(root_abs, rel_parts)
    profile = settings.compatibility_profile

    if contains_surrogateescape(name):
        issues.append(make_issue(
            severity="ERROR",
            issue_kind=ISSUE_KIND_COPY_BLOCKER,
            object_type=object_type,
            name=name,
            location=loc,
            relative_path=rp_raw,
            absolute_path=abs_path,
            problem="Filename contains bytes that are not valid Unicode text",
            detail="Linux can store arbitrary filename bytes; ordinary NTFS/Windows/macOS APIs expect Unicode names. Rename this item on Linux first.",
            check_id="unicode.invalid_bytes",
        ))

    ntfs_forbidden = ntfs_posix_namespace_forbidden_chars_in(name)
    if ntfs_forbidden:
        issues.append(make_issue(
            severity="ERROR",
            issue_kind=ISSUE_KIND_COPY_BLOCKER,
            object_type=object_type,
            name=name,
            location=loc,
            relative_path=rp_raw,
            absolute_path=abs_path,
            problem="Name contains a character not representable in the NTFS/POSIX namespace",
            detail=(
                f"Forbidden character(s): {format_bad_chars(ntfs_forbidden)}. "
                "The most permissive NTFS filename namespace allows Unicode except NUL and forward slash. "
                "A normal Linux scan should never see these inside one filename component, so this usually indicates unusual programmatic input."
            ),
            check_id="ntfs.posix_namespace_forbidden_char",
        ))

    if profile == PROFILE_WINDOWS:
        if ":" in name:
            issues.append(make_issue(
                severity="ERROR",
                issue_kind=ISSUE_KIND_WINDOWS_ACCESS,
                object_type=object_type,
                name=name,
                location=loc,
                relative_path=rp_raw,
                absolute_path=abs_path,
                problem="Colon in name is invalid/ambiguous in Windows",
                detail=(
                    "Windows treats ':' as path syntax, including drive-letter prefixes and NTFS alternate data streams "
                    "such as file.txt:Zone.Identifier. A permissive Linux NTFS driver may create a name containing ':', "
                    "but normal Windows tools cannot use it as an ordinary file/folder name. Mounting with windows_names should reject it before copy."
                ),
                check_id="windows.colon_or_ads_syntax",
            ))

        bad_chars = invalid_windows_chars_in(name)
        if bad_chars:
            issues.append(make_issue(
                severity="ERROR",
                issue_kind=ISSUE_KIND_WINDOWS_ACCESS,
                object_type=object_type,
                name=name,
                location=loc,
                relative_path=rp_raw,
                absolute_path=abs_path,
                problem="Windows-forbidden/control character in name",
                detail=(
                    f"Forbidden/control character(s): {format_bad_chars(bad_chars)}. "
                    "A permissive Linux NTFS driver may create this, but normal Windows tools cannot handle it. "
                    "Mounting with windows_names should reject it before copy."
                ),
                check_id="windows.forbidden_chars",
            ))

        if name.endswith(" ") or name.endswith("."):
            issues.append(make_issue(
                severity="ERROR",
                issue_kind=ISSUE_KIND_WINDOWS_ACCESS,
                object_type=object_type,
                name=name,
                location=loc,
                relative_path=rp_raw,
                absolute_path=abs_path,
                problem="Name ends with a space or dot",
                detail="NTFS may store this through nonstandard paths, but the Windows shell and normal Win32 paths handle it badly or reject it.",
                check_id="windows.trailing_space_or_dot",
            ))

        if is_windows_reserved_name(name):
            issues.append(make_issue(
                severity="ERROR",
                issue_kind=ISSUE_KIND_WINDOWS_ACCESS,
                object_type=object_type,
                name=name,
                location=loc,
                relative_path=rp_raw,
                absolute_path=abs_path,
                problem="Windows-reserved device name",
                detail="CON, PRN, AUX, NUL, COM1-COM9 and LPT1-LPT9 are reserved even with extensions, e.g. NUL.txt.",
                check_id="windows.reserved_device_name",
            ))

    units = utf16_units(name)
    if units > settings.max_component_utf16_units:
        issues.append(make_issue(
            severity="ERROR",
            issue_kind=ISSUE_KIND_COPY_BLOCKER,
            object_type=object_type,
            name=name,
            location=loc,
            relative_path=rp_raw,
            absolute_path=abs_path,
            problem="Name component too long for NTFS",
            detail=f"Component is {units} UTF-16 code units; configured NTFS component limit is {settings.max_component_utf16_units}.",
            check_id="ntfs.component_utf16_too_long",
        ))

    # Byte-aware checks are profile-dependent. Pure NTFS is UTF-16 based, so a
    # CrossRename/ext4 255-byte name warning is not a raw NTFS limitation and is
    # intentionally disabled there. Windows/macOS-oriented scans keep it as a
    # portability check; macOS treats the 255-byte component threshold as an
    # access-risk check.
    name_bytes = utf8_bytes(name)
    if profile == PROFILE_MACOS:
        byte_limit = settings.max_macos_component_utf8_bytes
        if name_bytes > byte_limit:
            issues.append(make_issue(
                severity="ERROR",
                issue_kind=ISSUE_KIND_MACOS_ACCESS,
                object_type=object_type,
                name=name,
                location=loc,
                relative_path=rp_raw,
                absolute_path=abs_path,
                problem="Name component too long for common macOS path APIs",
                detail=f"Component is {name_bytes} UTF-8 bytes; configured macOS/component byte limit is {byte_limit}.",
                check_id="macos.component_utf8_bytes_too_long",
            ))
    elif profile == PROFILE_WINDOWS and settings.check_crossrename_rules:
        byte_limit = settings.max_component_utf8_bytes
        if name_bytes > byte_limit:
            issues.append(make_issue(
                severity="WARNING",
                issue_kind=ISSUE_KIND_PORTABILITY,
                object_type=object_type,
                name=name,
                location=loc,
                relative_path=rp_raw,
                absolute_path=abs_path,
                problem="Name component exceeds CrossRename/Linux-style UTF-8 byte limit",
                detail=f"Component is {name_bytes} UTF-8 bytes; configured byte-aware limit is {byte_limit}. NTFS may still store it, but this mirrors CrossRename/ext4-style portability checks.",
                check_id="portability.component_utf8_bytes_too_long",
            ))

    if profile == PROFILE_MACOS and ":" in name:
        issues.append(make_issue(
            severity="WARNING",
            issue_kind=ISSUE_KIND_MACOS_ACCESS,
            object_type=object_type,
            name=name,
            location=loc,
            relative_path=rp_raw,
            absolute_path=abs_path,
            problem="Colon in name is awkward or blocked in macOS/Finder",
            detail=(
                "macOS/APFS can preserve valid UTF-8 names, but Finder and HFS-era path conventions treat ':' specially. "
                "Some macOS tools display, translate, or refuse colon names, so this is flagged in the macOS profile."
            ),
            check_id="macos.colon_finder_risk",
        ))

    if profile == PROFILE_MACOS and name.startswith("."):
        issues.append(make_issue(
            severity="WARNING",
            issue_kind=ISSUE_KIND_MACOS_ACCESS,
            object_type=object_type,
            name=name,
            location=loc,
            relative_path=rp_raw,
            absolute_path=abs_path,
            problem="Name starts with a dot in macOS/Finder",
            detail=(
                "Dot-prefixed names are valid POSIX names and common for configuration files, but they are hidden/awkward in Finder workflows "
                "and Finder's rename UI may reject creating such names. This is a macOS usability warning, not an NTFS copy blocker."
            ),
            check_id="macos.leading_dot_finder_risk",
        ))

    if settings.check_nfc and unicodedata.normalize("NFC", name) != name:
        issues.append(make_issue(
            severity="WARNING",
            issue_kind=ISSUE_KIND_PORTABILITY,
            object_type=object_type,
            name=name,
            location=loc,
            relative_path=rp_raw,
            absolute_path=abs_path,
            problem="Name is not Unicode NFC-normalized",
            detail="It may look identical to another name after normalization. Rename to NFC form for maximum portability.",
            check_id="unicode.not_nfc",
        ))

    return issues


def analyze_path(
    *,
    root_abs: str,
    rel_parts: list[str],
    object_type: str,
    settings: Settings,
) -> list[Issue]:
    issues: list[Issue] = []
    rp_raw = rel_raw(rel_parts)
    abs_path = absolute_from_rel(root_abs, rel_parts)
    loc = parent_location(root_abs, rel_parts)
    name = rel_parts[-1] if rel_parts else os.path.basename(root_abs)
    profile = settings.compatibility_profile

    # Depth: count components below selected root. For a file, this includes the filename.
    # This is a user-configurable comfort warning, not a hard NTFS/APFS/ext4 limit.
    depth = len(rel_parts)
    if settings.max_depth >= 0 and depth > settings.max_depth:
        issues.append(make_issue(
            severity="WARNING",
            issue_kind=ISSUE_KIND_PORTABILITY,
            object_type=object_type,
            name=name,
            location=loc,
            relative_path=rp_raw,
            absolute_path=abs_path,
            problem="Relative nesting depth exceeds configured comfort limit",
            detail=f"Depth is {depth}; configured warning threshold is {settings.max_depth}. Real limits are usually path-length limits, not a fixed folder-depth count.",
            check_id="path.depth_comfort_limit",
        ))

    # Pure NTFS/storage-level path estimate: component limits matter most, but NTFS also
    # has a very large extended path range. This is checked in all profiles.
    ntfs_path = windows_join(settings.dest_root, rel_parts)
    ntfs_path_units = utf16_units(ntfs_path)
    if ntfs_path_units > settings.max_extended_path_utf16_units:
        issues.append(make_issue(
            severity="ERROR",
            issue_kind=ISSUE_KIND_COPY_BLOCKER,
            object_type=object_type,
            name=name,
            location=loc,
            relative_path=rp_raw,
            absolute_path=abs_path,
            problem="Estimated path exceeds NTFS extended path range",
            detail=f"Estimated destination path has {ntfs_path_units} UTF-16 code units; configured NTFS extended path limit is {settings.max_extended_path_utf16_units}. Destination prefix: {settings.dest_root!r}.",
            check_id="ntfs.extended_path_too_long",
        ))

    if profile == PROFILE_WINDOWS and settings.warn_legacy_paths:
        if object_type == "folder" and ntfs_path_units > settings.max_dir_path_utf16_units:
            sev = "WARNING" if settings.long_paths_enabled else "ERROR"
            issues.append(make_issue(
                severity=sev,
                issue_kind=ISSUE_KIND_WINDOWS_ACCESS,
                object_type=object_type,
                name=name,
                location=loc,
                relative_path=rp_raw,
                absolute_path=abs_path,
                problem="Full Windows directory path is too long for legacy Windows APIs",
                detail=f"Estimated destination path has {ntfs_path_units} UTF-16 code units; configured directory limit is {settings.max_dir_path_utf16_units}. Destination prefix: {settings.dest_root!r}.",
                check_id="windows.legacy_dir_path_too_long",
            ))
        elif object_type != "folder" and ntfs_path_units > settings.max_file_path_utf16_units:
            sev = "WARNING" if settings.long_paths_enabled else "ERROR"
            issues.append(make_issue(
                severity=sev,
                issue_kind=ISSUE_KIND_WINDOWS_ACCESS,
                object_type=object_type,
                name=name,
                location=loc,
                relative_path=rp_raw,
                absolute_path=abs_path,
                problem="Full Windows file path is too long for legacy Windows APIs",
                detail=f"Estimated destination path has {ntfs_path_units} UTF-16 code units; configured file limit is {settings.max_file_path_utf16_units}. Destination prefix: {settings.dest_root!r}.",
                check_id="windows.legacy_file_path_too_long",
            ))

    if profile == PROFILE_MACOS:
        mac_path = posix_join(settings.dest_root, rel_parts)
        mac_path_bytes = utf8_bytes(mac_path)
        if mac_path_bytes > settings.max_macos_path_utf8_bytes:
            issues.append(make_issue(
                severity="ERROR",
                issue_kind=ISSUE_KIND_MACOS_ACCESS,
                object_type=object_type,
                name=name,
                location=loc,
                relative_path=rp_raw,
                absolute_path=abs_path,
                problem="Estimated path is too long for common macOS path APIs",
                detail=f"Estimated destination path has {mac_path_bytes} UTF-8 bytes; configured macOS path limit is {settings.max_macos_path_utf8_bytes}. Destination prefix: {settings.dest_root!r}.",
                check_id="macos.path_utf8_bytes_too_long",
            ))

    return issues

def analyze_entry_name_and_path(
    *,
    root_abs: str,
    name: str,
    rel_parts: list[str],
    object_type: str,
    settings: Settings,
) -> list[Issue]:
    return analyze_component(root_abs=root_abs, name=name, rel_parts=rel_parts, object_type=object_type, settings=settings) + analyze_path(root_abs=root_abs, rel_parts=rel_parts, object_type=object_type, settings=settings)


def classify_direntry(entry: os.DirEntry[str]) -> str:
    try:
        if entry.is_symlink():
            return "symlink"
        if entry.is_dir(follow_symlinks=False):
            return "folder"
        if entry.is_file(follow_symlinks=False):
            return "file"
        return "special"
    except OSError:
        return "unknown"


def count_entries(root: str, cancel: Optional[threading.Event] = None) -> int:
    total = 0
    stack = [root]
    while stack:
        if cancel and cancel.is_set():
            break
        current = stack.pop()
        try:
            with os.scandir(current) as it:
                for entry in it:
                    total += 1
                    try:
                        if entry.is_dir(follow_symlinks=False) and not entry.is_symlink():
                            stack.append(entry.path)
                    except OSError:
                        pass
        except OSError:
            # Permission errors will be reported in the real scan.
            pass
    return total


def scan_tree(
    root: str,
    settings: Settings,
    progress_cb: Optional[Callable[[int, int, str], None]] = None,
    cancel: Optional[threading.Event] = None,
) -> tuple[list[Issue], dict]:
    root_abs = os.path.abspath(root)
    issues: list[Issue] = []
    summary = {
        "app": APP_NAME,
        "version": APP_VERSION,
        "root": safe_display(root_abs),
        "compatibility_profile": settings.compatibility_profile,
        "compatibility_profile_label": profile_label(settings.compatibility_profile),
        "dest_root": settings.dest_root,
        "total_entries": 0,
        "files": 0,
        "folders": 0,
        "symlinks": 0,
        "special": 0,
        "errors": 0,
        "warnings": 0,
        "copy_blockers": 0,
        "windows_access_risks": 0,
        "macos_access_risks": 0,
        "os_access_risks": 0,
        "portability_warnings": 0,
        "max_depth_seen": 0,
        "max_ntfs_path_utf16_units": 0,
        "max_windows_path_utf16_units": 0,
        "max_macos_path_utf8_bytes": 0,
        "started_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "completed": False,
        "cancelled": False,
        "settings": asdict(settings),
    }

    total = count_entries(root_abs, cancel)
    summary["total_entries"] = total
    done = 0

    def report_progress(path: str) -> None:
        if progress_cb:
            progress_cb(done, total, path)

    stack: list[tuple[str, list[str]]] = [(root_abs, [])]
    while stack:
        if cancel and cancel.is_set():
            summary["cancelled"] = True
            break

        current, parent_rel = stack.pop()
        try:
            with os.scandir(current) as it:
                entries = list(it)
        except OSError as e:
            name = parent_rel[-1] if parent_rel else os.path.basename(root_abs)
            issues.append(make_issue(
                severity="ERROR",
                issue_kind=ISSUE_KIND_COPY_BLOCKER,
                object_type="folder",
                name=name,
                location=parent_location(root_abs, parent_rel) if parent_rel else os.path.dirname(root_abs),
                relative_path=rel_raw(parent_rel),
                absolute_path=absolute_from_rel(root_abs, parent_rel) if parent_rel else root_abs,
                problem="Could not read folder",
                detail=f"{type(e).__name__}: {e}",
                check_id="scan.folder_unreadable",
            ))
            continue

        if settings.check_casefold_collisions and settings.compatibility_profile in (PROFILE_WINDOWS, PROFILE_MACOS):
            buckets: dict[str, list[os.DirEntry[str]]] = {}
            for entry in entries:
                buckets.setdefault(windows_collision_key(entry.name), []).append(entry)
            for bucket in buckets.values():
                if len(bucket) > 1:
                    names = ", ".join(repr(safe_display(e.name)) for e in bucket)
                    os_kind = profile_os_issue_kind(settings.compatibility_profile) or ISSUE_KIND_PORTABILITY
                    if settings.compatibility_profile == PROFILE_MACOS:
                        problem = "Name collision under macOS-style case/Unicode comparison"
                        detail = f"These names are distinct here but may collide for default case-insensitive macOS/Finder/APFS-style use after NFC + casefold: {names}"
                        check_id = "macos.case_or_unicode_collision"
                    else:
                        problem = "Name collision under Windows-style case/Unicode comparison"
                        detail = f"These names are distinct here but may collide for normal Windows users/tools after NFC + casefold: {names}"
                        check_id = "windows.case_or_unicode_collision"
                    for entry in bucket:
                        rel_parts = parent_rel + [entry.name]
                        issues.append(make_issue(
                            severity="ERROR",
                            issue_kind=os_kind,
                            object_type=classify_direntry(entry),
                            name=entry.name,
                            location=parent_location(root_abs, rel_parts),
                            relative_path=rel_raw(rel_parts),
                            absolute_path=absolute_from_rel(root_abs, rel_parts),
                            problem=problem,
                            detail=detail,
                            check_id=check_id,
                        ))

        # Reverse-sort to make traversal stable-ish while using a stack.
        for entry in sorted(entries, key=lambda e: safe_display(e.name), reverse=True):
            if cancel and cancel.is_set():
                summary["cancelled"] = True
                break
            done += 1
            rel_parts = parent_rel + [entry.name]
            object_type = classify_direntry(entry)
            summary["max_depth_seen"] = max(summary["max_depth_seen"], len(rel_parts))
            ntfs_path_units = utf16_units(windows_join(settings.dest_root, rel_parts))
            summary["max_ntfs_path_utf16_units"] = max(summary["max_ntfs_path_utf16_units"], ntfs_path_units)
            summary["max_windows_path_utf16_units"] = max(summary["max_windows_path_utf16_units"], ntfs_path_units)
            summary["max_macos_path_utf8_bytes"] = max(
                summary["max_macos_path_utf8_bytes"], utf8_bytes(posix_join(settings.dest_root, rel_parts))
            )

            if object_type in ("file", "folder", "symlink", "special", "unknown"):
                issues.extend(analyze_entry_name_and_path(root_abs=root_abs, name=entry.name, rel_parts=rel_parts, object_type=object_type, settings=settings))

            if object_type == "file":
                summary["files"] += 1
                if settings.warn_hardlinks:
                    try:
                        st = entry.stat(follow_symlinks=False)
                        if st.st_nlink > 1:
                            issues.append(make_issue(
                                severity="WARNING",
                                issue_kind=ISSUE_KIND_PORTABILITY,
                                object_type="file",
                                name=entry.name,
                                location=parent_location(root_abs, rel_parts),
                                relative_path=rel_raw(rel_parts),
                                absolute_path=absolute_from_rel(root_abs, rel_parts),
                                problem="File has multiple hard links",
                                detail=f"Link count is {st.st_nlink}. A normal copy to NTFS may duplicate the file rather than preserving hard-link identity.",
                                check_id="metadata.hardlink_identity",
                            ))
                    except OSError:
                        pass
            elif object_type == "folder":
                summary["folders"] += 1
                stack.append((entry.path, rel_parts))
            elif object_type == "symlink":
                summary["symlinks"] += 1
                if settings.warn_symlinks:
                    try:
                        target = os.readlink(entry.path)
                    except OSError:
                        target = "<unreadable>"
                    issues.append(make_issue(
                        severity="WARNING",
                        issue_kind=ISSUE_KIND_PORTABILITY,
                        object_type="symlink",
                        name=entry.name,
                        location=parent_location(root_abs, rel_parts),
                        relative_path=rel_raw(rel_parts),
                        absolute_path=absolute_from_rel(root_abs, rel_parts),
                        problem="Symbolic link encountered",
                        detail=f"Symlink target: {safe_display(target)}. Ordinary copies may skip it, follow it, or create a Windows link with different semantics.",
                        check_id="metadata.symlink_semantics",
                    ))
            else:
                summary["special"] += 1
                issues.append(make_issue(
                    severity="ERROR",
                    issue_kind=ISSUE_KIND_COPY_BLOCKER,
                    object_type=object_type,
                    name=entry.name,
                    location=parent_location(root_abs, rel_parts),
                    relative_path=rel_raw(rel_parts),
                    absolute_path=absolute_from_rel(root_abs, rel_parts),
                    problem="Special/non-regular filesystem object",
                    detail="This is not a normal file/folder/symlink. Examples include sockets, FIFOs and device nodes. Do not expect a normal NTFS copy to preserve it.",
                    check_id="metadata.special_file",
                ))

            report_progress(rel_display(rel_parts))

    issues.sort(key=lambda x: (
        SEVERITY_ORDER.get(x.severity, 9),
        KIND_ORDER.get(x.issue_kind, 9),
        x.relative_path,
        x.problem,
    ))
    summary["errors"] = sum(1 for i in issues if i.severity == "ERROR")
    summary["warnings"] = sum(1 for i in issues if i.severity == "WARNING")

    # Keep both concepts separate:
    # - issue_rows count table/report rows; one file/folder can have several issues.
    # - problematic_items and the emoji counts count unique filesystem items, assigning
    #   each item to its most serious issue class. This makes the displayed total equal
    #   the sum of the three emoji counts.
    summary["issue_rows"] = len(issues)
    summary["copy_blocker_issues"] = sum(1 for i in issues if i.issue_kind == ISSUE_KIND_COPY_BLOCKER)
    summary["windows_access_risk_issues"] = sum(1 for i in issues if i.issue_kind == ISSUE_KIND_WINDOWS_ACCESS)
    summary["macos_access_risk_issues"] = sum(1 for i in issues if i.issue_kind == ISSUE_KIND_MACOS_ACCESS)
    summary["os_access_risk_issues"] = summary["windows_access_risk_issues"] + summary["macos_access_risk_issues"]
    summary["portability_warning_issues"] = sum(1 for i in issues if i.issue_kind == ISSUE_KIND_PORTABILITY)
    item_counts = problematic_item_kind_counts(issues)
    summary["problematic_items"] = sum(item_counts.values())
    summary["copy_blockers"] = item_counts[ISSUE_KIND_COPY_BLOCKER]
    summary["windows_access_risks"] = item_counts[ISSUE_KIND_WINDOWS_ACCESS]
    summary["macos_access_risks"] = item_counts[ISSUE_KIND_MACOS_ACCESS]
    summary["os_access_risks"] = summary["windows_access_risks"] + summary["macos_access_risks"]
    summary["portability_warnings"] = item_counts[ISSUE_KIND_PORTABILITY]
    summary["problematic_item_counting"] = "unique paths counted once by most serious issue class"
    summary["completed_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    summary["completed"] = not summary["cancelled"]
    return issues, summary


def problematic_item_kind_counts(issues: Sequence[Issue]) -> dict[str, int]:
    """Count unique files/folders once, by their most serious issue class.

    The table is intentionally issue-based: one path can appear multiple times if it
    has multiple independent problems. The summary label is item-based, so a path
    with both a Windows-access risk and a portability warning is counted once under
    the more serious Windows-access-risk bucket.
    """
    by_path: dict[str, str] = {}
    for issue in issues:
        current = by_path.get(issue.absolute_path)
        if current is None or KIND_ORDER.get(issue.issue_kind, 99) < KIND_ORDER.get(current, 99):
            by_path[issue.absolute_path] = issue.issue_kind
    return {
        ISSUE_KIND_COPY_BLOCKER: sum(1 for kind in by_path.values() if kind == ISSUE_KIND_COPY_BLOCKER),
        ISSUE_KIND_WINDOWS_ACCESS: sum(1 for kind in by_path.values() if kind == ISSUE_KIND_WINDOWS_ACCESS),
        ISSUE_KIND_MACOS_ACCESS: sum(1 for kind in by_path.values() if kind == ISSUE_KIND_MACOS_ACCESS),
        ISSUE_KIND_PORTABILITY: sum(1 for kind in by_path.values() if kind == ISSUE_KIND_PORTABILITY),
    }


def edited_record_to_issue(record: EditedRecord) -> Issue:
    """Represent an edited item as a non-reporting GUI row."""
    return make_issue(
        severity="INFO",
        issue_kind=ISSUE_KIND_EDITED,
        object_type=record.object_type,
        name=record.new_name,
        location=record.location,
        relative_path=record.relative_path,
        absolute_path=record.new_absolute_path,
        problem="Edited item",
        detail=(
            f"Renamed in this app from {safe_display(record.old_name)!r} to "
            f"{safe_display(record.new_name)!r} at {record.edited_at}. This row is shown because "
            "Show edited items is enabled; it is not counted as an unresolved compatibility problem. "
            "Edited rows are cleared when a new scan starts."
        ),
        check_id="user.edited_item",
    )


def issue_to_report_dict(issue: Issue) -> dict:
    """Return a JSON/CSV-safe issue dict without losing path operation data in memory."""
    d = asdict(issue)
    for key in ("name", "location", "relative_path", "absolute_path", "problem", "detail", "check_id"):
        if key in d and isinstance(d[key], str):
            d[key] = safe_display(d[key])
    return d


def write_csv(path: str, issues: list[Issue], summary: dict) -> None:
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow([f"{APP_NAME} Report"])
        for k, v in summary.items():
            if k == "settings":
                writer.writerow([k, json.dumps(v, ensure_ascii=False)])
            else:
                writer.writerow([k, v])
        writer.writerow([])
        writer.writerow([
            "severity", "issue_kind", "icon", "object_type", "name", "location", "relative_path",
            "absolute_path", "problem", "detail", "check_id", "can_copy_to_ntfs", "normal_windows_access_ok",
        ])
        for issue in issues:
            writer.writerow([
                issue.severity, issue.issue_kind, issue.icon, issue.object_type, safe_display(issue.name), safe_display(issue.location),
                safe_display(issue.relative_path), safe_display(issue.absolute_path), issue.problem, issue.detail, issue.check_id,
                issue.can_copy_to_ntfs, issue.normal_windows_access_ok,
            ])


def write_json(path: str, issues: list[Issue], summary: dict) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"summary": summary, "issues": [issue_to_report_dict(i) for i in issues]}, f, ensure_ascii=False, indent=2)


def print_summary(issues: list[Issue], summary: dict) -> None:
    profile = summary.get("compatibility_profile", PROFILE_WINDOWS)
    os_icon = profile_os_icon(profile)
    os_count = summary.get("os_access_risks", 0)
    print(f"{APP_NAME} {APP_VERSION}")
    print(f"Root: {summary['root']}")
    print(f"Compatibility profile: {summary.get('compatibility_profile_label', profile_label(profile))}")
    print(f"Destination prefix used for path estimates: {summary['dest_root'] or '(blank; source-relative only)'}")
    print(
        f"Entries: {summary['total_entries']} | files={summary['files']} folders={summary['folders']} "
        f"symlinks={summary['symlinks']} special={summary['special']}"
    )
    print(
        f"Issue rows: {summary.get('issue_rows', len(issues))} | errors={summary['errors']} warnings={summary['warnings']}"
    )
    print(
        f"Problematic items: {summary.get('problematic_items', 0)} | "
        f"⛔ {summary['copy_blockers']} {os_icon} {os_count} ⚠️ {summary['portability_warnings']} "
        "(unique paths counted once by most serious issue class)"
    )
    print(f"Max depth seen: {summary['max_depth_seen']}")
    print(f"Max estimated NTFS path length: {summary.get('max_ntfs_path_utf16_units', summary.get('max_windows_path_utf16_units', 0))} UTF-16 code units")
    if profile == PROFILE_MACOS:
        print(f"Max estimated macOS path length: {summary.get('max_macos_path_utf8_bytes', 0)} UTF-8 bytes")
    print()
    for issue in issues[:200]:
        print(f"[{issue.severity}] {issue.icon} {issue.issue_kind} | {issue.object_type}: {safe_display(issue.relative_path)}")
        print(f"  {issue.problem}: {issue.detail}")
    if len(issues) > 200:
        print(f"... {len(issues) - 200} more issues not printed. Use --csv or --json for full report.")


# ----------------------------- PySide6 GUI -----------------------------

def open_with_default_app(path: str) -> bool:
    """Open a file/folder with the OS default handler. Requires PySide6 in GUI mode."""
    from PySide6.QtCore import QUrl
    from PySide6.QtGui import QDesktopServices

    return bool(QDesktopServices.openUrl(QUrl.fromLocalFile(path)))


def reveal_in_file_manager(path: str) -> bool:
    """Best effort reveal/select. Uses OS-specific helpers; logs exceptions and returns False."""
    system = platform.system().lower()
    try:
        if system == "windows":
            if os.path.isdir(path):
                return open_with_default_app(path)
            subprocess.Popen(["explorer", "/select,", os.path.normpath(path)])
            return True
        if system == "darwin":
            subprocess.Popen(["/usr/bin/open", "-R", path])
            return True
        # Linux/Unix: DBus FileManager1 is the closest cross-desktop reveal API, but
        # it is not universally available. Fall back to opening the parent folder.
        parent = path if os.path.isdir(path) else os.path.dirname(path)
        if parent:
            return open_with_default_app(parent)
    except Exception:
        logging.getLogger(__name__).warning(
            "Could not reveal %r in the file manager", path, exc_info=True
        )
    return False


class _ScanWorker:
    """Small QObject worker, created lazily to keep CLI free of PySide6 imports."""
    def __init__(self, root: str, settings: Settings) -> None:
        from PySide6.QtCore import QObject, Signal

        class Worker(QObject):  # type: ignore[misc,valid-type]
            progress = Signal(int, int, str)
            finished = Signal(list, dict)
            failed = Signal(str)

            def __init__(self, root: str, settings: Settings) -> None:
                super().__init__()
                self.root = root
                self.settings = settings
                self.cancel_event = threading.Event()

            def run(self) -> None:
                try:
                    def cb(done: int, total: int, path: str) -> None:
                        self.progress.emit(done, total, path)
                    issues, summary = scan_tree(self.root, self.settings, cb, self.cancel_event)
                    self.finished.emit(issues, summary)
                except Exception as exc:  # pragma: no cover - GUI path
                    self.failed.emit(f"{type(exc).__name__}: {exc}")

            def cancel(self) -> None:
                self.cancel_event.set()

        self.obj = Worker(root, settings)


def _quote_cmd(parts: Sequence[str]) -> str:
    return " ".join(shlex.quote(p) for p in parts)


def _try_reexec_with_uv_for_pyside6() -> bool:
    """Re-run the script through uv with PySide6 if the user launched it the wrong way.

    This handles common invocations such as:
      uv run python src/ntfsafe.py
      python3 src/ntfsafe.py
    which do not necessarily consume the PEP 723 inline metadata.
    """
    if os.environ.get("NTFS_PREFLIGHT_NO_UV_REEXEC") == "1":
        return False
    if os.environ.get("NTFS_PREFLIGHT_UV_REEXEC") == "1":
        return False
    uv = shutil.which("uv")
    if not uv:
        return False
    script = Path(__file__).resolve()
    if not script.is_file():
        return False
    env = os.environ.copy()
    env["NTFS_PREFLIGHT_UV_REEXEC"] = "1"
    cmd = [uv, "run", "--with", "PySide6>=6.7,<7", "python", str(script), *sys.argv[1:]]
    print("PySide6 is missing in the current Python environment; re-running with uv:", file=sys.stderr)
    print(_quote_cmd(cmd), file=sys.stderr)
    os.execvpe(uv, cmd, env)
    return True


def run_gui() -> int:
    try:
        from PySide6.QtCore import (
            QAbstractTableModel,
            QEvent,
            QModelIndex,
            Qt,
            QThread,
            QTimer,
            QUrl,
        )
        from PySide6.QtGui import (
            QAction,
            QColor,
            QDesktopServices,
            QGuiApplication,
            QPalette,
        )
        from PySide6.QtWidgets import (
            QAbstractItemView,
            QApplication,
            QCheckBox,
            QComboBox,
            QDialog,
            QDialogButtonBox,
            QFileDialog,
            QGridLayout,
            QGroupBox,
            QHBoxLayout,
            QHeaderView,
            QLabel,
            QLineEdit,
            QMainWindow,
            QMessageBox,
            QProgressBar,
            QPushButton,
            QSpinBox,
            QStatusBar,
            QTableView,
            QTextBrowser,
            QToolTip,
            QVBoxLayout,
            QWidget,
        )
    except Exception as e:
        if _try_reexec_with_uv_for_pyside6():
            return 0
        script = Path(__file__).resolve()
        print("PySide6 is required for the GUI, but it is not installed in the Python environment that launched this script.", file=sys.stderr)
        print("Recommended uv commands:", file=sys.stderr)
        print(f"  uv run --script {shlex.quote(str(script))}", file=sys.stderr)
        print(f"  uv run --with 'PySide6>=6.7,<7' python {shlex.quote(str(script))}", file=sys.stderr)
        print("Avoid this form for standalone PEP 723 scripts unless you also use --with or a pyproject.toml:", file=sys.stderr)
        print(f"  uv run python {shlex.quote(str(script))}", file=sys.stderr)
        print(f"Import error: {e}", file=sys.stderr)
        return 2

    def _gui_colors(widget=None) -> dict[str, str]:
        color_scheme = QGuiApplication.styleHints().colorScheme()
        if color_scheme == Qt.ColorScheme.Dark:
            is_dark = True
        elif color_scheme == Qt.ColorScheme.Light:
            is_dark = False
        else:
            pal = widget.palette() if widget is not None else QApplication.palette()
            is_dark = pal.color(QPalette.ColorRole.Base).lightness() < 128
        if is_dark:
            return {
                "window": "#111827", "panel": "#1f2937", "base": "#0f172a",
                "alternate": "#111827", "text": "#f9fafb", "muted": "#d1d5db",
                "border": "#64748b", "header": "#374151", "header_hover": "#475569",
                "selection": "#2563eb", "selection_text": "#ffffff",
                "scroll_track": "#1f2937", "scroll_handle": "#60a5fa", "scroll_handle_hover": "#93c5fd",
                "scroll_button": "#2563eb", "scroll_button_hover": "#1d4ed8",
                "input": "#0f172a", "input_focus": "#1e293b", "button": "#374151",
                "button_hover": "#475569", "button_text": "#f9fafb",
            }
        return {
            "window": "#f8fafc", "panel": "#ffffff", "base": "#ffffff",
            "alternate": "#eef2ff", "text": "#111827", "muted": "#334155",
            "border": "#64748b", "header": "#dbeafe", "header_hover": "#bfdbfe",
            "selection": "#2563eb", "selection_text": "#ffffff",
            "scroll_track": "#cbd5e1", "scroll_handle": "#0f172a", "scroll_handle_hover": "#1d4ed8",
            "scroll_button": "#334155", "scroll_button_hover": "#0f172a",
            "input": "#ffffff", "input_focus": "#f8fafc", "button": "#e2e8f0",
            "button_hover": "#bfdbfe", "button_text": "#111827",
        }

    def _gui_palette(c: dict[str, str]):
        palette = QPalette()
        for role, color in (
            (QPalette.ColorRole.Window, c["window"]),
            (QPalette.ColorRole.WindowText, c["text"]),
            (QPalette.ColorRole.Base, c["base"]),
            (QPalette.ColorRole.AlternateBase, c["alternate"]),
            (QPalette.ColorRole.ToolTipBase, c["panel"]),
            (QPalette.ColorRole.ToolTipText, c["text"]),
            (QPalette.ColorRole.Text, c["text"]),
            (QPalette.ColorRole.Button, c["button"]),
            (QPalette.ColorRole.ButtonText, c["button_text"]),
            (QPalette.ColorRole.BrightText, c["selection_text"]),
            (QPalette.ColorRole.Highlight, c["selection"]),
            (QPalette.ColorRole.HighlightedText, c["selection_text"]),
            (QPalette.ColorRole.Link, c["selection"]),
            (QPalette.ColorRole.LinkVisited, c["selection"]),
            (QPalette.ColorRole.PlaceholderText, c["muted"]),
        ):
            palette.setColor(role, QColor(color))
        disabled = QPalette.ColorGroup.Disabled
        palette.setColor(disabled, QPalette.ColorRole.WindowText, QColor(c["muted"]))
        palette.setColor(disabled, QPalette.ColorRole.Text, QColor(c["muted"]))
        palette.setColor(disabled, QPalette.ColorRole.ButtonText, QColor(c["muted"]))
        palette.setColor(disabled, QPalette.ColorRole.PlaceholderText, QColor(c["muted"]))
        return palette

    def table_view_stylesheet(widget=None) -> str:
        c = _gui_colors(widget)
        return f"""
            QTableView {{
                background-color: {c['base']};
                alternate-background-color: {c['alternate']};
                color: {c['text']};
                gridline-color: {c['border']};
                selection-background-color: {c['selection']};
                selection-color: {c['selection_text']};
            }}
            QTableView::item {{
                padding: 2px 6px;
                color: {c['text']};
            }}
            QTableView::item:selected {{
                background-color: {c['selection']};
                color: {c['selection_text']};
            }}
            QHeaderView {{
                background-color: {c['header']};
                color: {c['text']};
            }}
            QHeaderView::section {{
                padding: 6px 8px;
                font-weight: 600;
                color: {c['text']};
                border: 0px;
                border-right: 1px solid {c['border']};
                border-bottom: 1px solid {c['border']};
                background-color: {c['header']};
            }}
            QHeaderView::section:hover {{
                background-color: {c['header_hover']};
            }}
            QTableCornerButton::section {{
                background-color: {c['header']};
                border: 0px;
                border-right: 1px solid {c['border']};
                border-bottom: 1px solid {c['border']};
            }}
            QScrollBar:horizontal {{
                height: 24px;
                margin: 2px 28px 2px 28px;
                border: 1px solid {c['border']};
                border-radius: 10px;
                background-color: {c['scroll_track']};
            }}
            QScrollBar::handle:horizontal {{
                min-width: 96px;
                border: 2px solid {c['base']};
                border-radius: 9px;
                background-color: {c['scroll_handle']};
            }}
            QScrollBar::handle:horizontal:hover {{
                background-color: {c['scroll_handle_hover']};
            }}
            QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{
                width: 24px;
                border: 1px solid {c['border']};
                border-radius: 9px;
                background-color: {c['scroll_button']};
            }}
            QScrollBar::add-line:horizontal:hover, QScrollBar::sub-line:horizontal:hover {{
                background-color: {c['scroll_button_hover']};
            }}
            QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal {{
                background: transparent;
            }}
            QScrollBar:vertical {{
                width: 22px;
                margin: 2px 2px 2px 2px;
                border: 1px solid {c['border']};
                border-radius: 9px;
                background-color: {c['scroll_track']};
            }}
            QScrollBar::handle:vertical {{
                min-height: 18px;
                border: 2px solid {c['base']};
                border-radius: 8px;
                background-color: {c['scroll_handle']};
            }}
            QScrollBar::handle:vertical:hover {{
                background-color: {c['scroll_handle_hover']};
            }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
                height: 0px;
                border: 0px;
                background: transparent;
            }}
            QScrollBar::add-line:vertical:hover, QScrollBar::sub-line:vertical:hover {{
                background: transparent;
            }}
            QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{
                background: transparent;
            }}
        """

    class IssueTableModel(QAbstractTableModel):
        COLUMNS = ["", "Kind", "Type", "Name", "Location", "Problem", "Detail"]

        def __init__(self, rename_callback: Optional[Callable[[Issue, str], bool]] = None) -> None:
            super().__init__()
            self.issues: list[Issue] = []
            self.items: list[ProblemItem] = []
            self.edited_paths: set[str] = set()
            self.rename_callback = rename_callback
            self.sort_column: int = -1
            self.sort_order: Qt.SortOrder = Qt.SortOrder.AscendingOrder

        def display_value(self, item: ProblemItem, column: int) -> str:
            issue = item.primary_issue
            values = [
                icon_text_for_item(item),
                kind_text_for_item(item),
                issue.object_type,
                safe_display(issue.name),
                safe_display(issue.location),
                problem_text_for_item(item),
                detail_text_for_item(item, multiline=False),
            ]
            return values[column] if 0 <= column < len(values) else ""

        def set_issues(self, issues: list[Issue], edited_paths: Optional[set[str]] = None) -> None:
            self.beginResetModel()
            self.issues = list(issues)
            self.edited_paths = set(edited_paths or set())
            self.items = group_problem_items(self.issues, self.edited_paths)
            self._sort_in_place()
            self.endResetModel()

        def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: N802
            return 0 if parent.isValid() else len(self.items)

        def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: N802
            return 0 if parent.isValid() else len(self.COLUMNS)

        def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole):
            if not index.isValid() or not (0 <= index.row() < len(self.items)):
                return None
            item = self.items[index.row()]
            if role in (Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.EditRole):
                return self.display_value(item, index.column())
            if role == Qt.ItemDataRole.ToolTipRole:
                return self.tooltip_for(item, index.column())
            if role == Qt.ItemDataRole.BackgroundRole and item.edited:
                return QColor(245, 158, 11, 46)
            if role == Qt.ItemDataRole.ForegroundRole and item.is_multi_problem and index.column() == 0:
                return QColor("#f97316")
            if role == Qt.ItemDataRole.FontRole and item.is_multi_problem:
                font = QApplication.font()
                font.setBold(True)
                return font
            return None

        def tooltip_for(self, item: ProblemItem, column: int) -> str:
            issue = item.primary_issue
            if column == 4:
                return safe_display(issue.absolute_path)
            if column == 5:
                return detail_text_for_item(item, multiline=True)
            if column == 6:
                return detail_text_for_item(item, multiline=True)
            if column == 3:
                return f"{safe_display(issue.name)}\n{safe_display(issue.absolute_path)}"
            if column == 0 and item.is_multi_problem:
                return "This item has more than one problem. Multi-problem rows stay pinned at the end in A-Z Problem order, independently of column sorting."
            if item.edited:
                return "This item was renamed from inside the app since the last scan. Use the Show edited items checkbox to show or hide edited rows. Starting a new scan clears these rows and checks the items again."
            return self.display_value(item, column)

        def flags(self, index: QModelIndex):
            flags = super().flags(index)
            if index.isValid() and index.column() == 3:
                flags |= Qt.ItemFlag.ItemIsEditable
            return flags

        def setData(self, index: QModelIndex, value, role: int = Qt.ItemDataRole.EditRole):  # noqa: N802
            if role != Qt.ItemDataRole.EditRole or not index.isValid() or index.column() != 3:
                return False
            issue = self.issue_at(index.row())
            if issue is None or self.rename_callback is None:
                return False
            new_name = str(value)
            return bool(self.rename_callback(issue, new_name))

        def headerData(self, section: int, orientation: Qt.Orientation, role: int = Qt.ItemDataRole.DisplayRole):  # noqa: N802
            if role == Qt.ItemDataRole.DisplayRole and orientation == Qt.Orientation.Horizontal:
                return self.COLUMNS[section]
            return None

        def sort(self, column: int, order: Qt.SortOrder = Qt.SortOrder.AscendingOrder):  # noqa: N802
            if not (0 <= column < self.columnCount()):
                return
            self.sort_column = column
            self.sort_order = order
            self.layoutAboutToBeChanged.emit()
            self._sort_in_place()
            self.layoutChanged.emit()

        def _sort_in_place(self) -> None:
            single_problem_items = [item for item in self.items if not item.is_multi_problem]
            multi_problem_items = [item for item in self.items if item.is_multi_problem]
            if self.sort_column >= 0:
                single_problem_items.sort(
                    key=lambda item: (
                        self.display_value(item, self.sort_column).casefold(),
                        safe_display(item.primary_issue.absolute_path).casefold(),
                        problem_text_for_item(item).casefold(),
                    ),
                    reverse=self.sort_order == Qt.SortOrder.DescendingOrder,
                )
            multi_problem_items.sort(
                key=lambda item: (
                    problem_text_for_item(item).casefold(),
                    safe_display(item.primary_issue.absolute_path).casefold(),
                )
            )
            self.items = single_problem_items + multi_problem_items

        def item_at(self, row: int) -> Optional[ProblemItem]:
            if 0 <= row < len(self.items):
                return self.items[row]
            return None

        def issue_at(self, row: int) -> Optional[Issue]:
            item = self.item_at(row)
            return item.primary_issue if item else None


    class IssueTableView(QTableView):
        """Table view with file-manager-like tooltips and smoother scrolling."""

        def __init__(self) -> None:
            super().__init__()
            self.setMouseTracking(True)
            self.setAlternatingRowColors(True)
            self.setHorizontalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
            self.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
            self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
            self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
            self.horizontalScrollBar().setSingleStep(24)
            self.verticalScrollBar().setSingleStep(20)
            self.setStyleSheet(table_view_stylesheet(self))

        def refresh_scrollbar_metrics(self) -> None:
            """Let Qt recompute scrollbar ranges/thumb size after layout changes."""
            self.updateGeometry()
            self.viewport().updateGeometry()
            self.viewport().update()
            # In per-pixel scroll mode, the page step should track the visible
            # viewport height. This keeps the vertical thumb proportional to the
            # amount of the table that is actually visible when the window is
            # resized or the advanced-options panel is expanded/collapsed.
            self.verticalScrollBar().setPageStep(max(1, self.viewport().height()))

        def resizeEvent(self, event):  # noqa: N802
            super().resizeEvent(event)
            self.refresh_scrollbar_metrics()

        def viewportEvent(self, event):  # noqa: N802
            if event.type() == QEvent.Type.ToolTip:
                index = self.indexAt(event.pos())
                if index.isValid():
                    text = str(index.data(Qt.ItemDataRole.DisplayRole) or "")
                    width = max(0, self.columnWidth(index.column()) - 24)
                    truncated = bool(text) and self.fontMetrics().horizontalAdvance(text) > width
                    model = self.model()
                    item = model.item_at(index.row()) if hasattr(model, "item_at") else None
                    tooltip = None
                    if item is not None:
                        if index.column() == 4:
                            # Location tooltip: show one useful thing only, the full path.
                            tooltip = safe_display(item.primary_issue.absolute_path)
                        elif index.column() == 5:
                            # Problem tooltip: all problems plus their explanatory details.
                            tooltip = detail_text_for_item(item, multiline=True)
                        elif index.column() == 6:
                            tooltip = detail_text_for_item(item, multiline=True)
                        elif index.column() == 3:
                            tooltip = f"{safe_display(item.primary_issue.name)}\n{safe_display(item.primary_issue.absolute_path)}"
                        elif index.column() == 0 and item.is_multi_problem:
                            tooltip = model.tooltip_for(item, index.column())
                        elif truncated:
                            tooltip = text
                    elif truncated:
                        tooltip = text
                    if tooltip:
                        QToolTip.showText(event.globalPos(), tooltip, self.viewport())
                        return True
                QToolTip.hideText()
                return True
            return super().viewportEvent(event)

    class MainWindow(QMainWindow):
        def __init__(self) -> None:
            super().__init__()
            self.setWindowTitle(f"{APP_NAME} {APP_VERSION}")
            self.resize(1280, 760)
            self.issues: list[Issue] = []
            self.edited_records: dict[str, EditedRecord] = {}
            self.summary: dict = {}
            self.thread: Optional[QThread] = None
            self.worker = None
            self.model = IssueTableModel(self.rename_issue)
            self._build_ui()

        def _build_ui(self) -> None:
            central = QWidget()
            self.setCentralWidget(central)
            root_layout = QVBoxLayout(central)
            root_layout.setContentsMargins(12, 12, 12, 10)
            root_layout.setSpacing(8)

            title = QLabel(f"<b>{APP_NAME}</b> — scan before copying to an NTFS drive")
            title.setTextFormat(Qt.TextFormat.RichText)
            title.setWordWrap(True)
            root_layout.addWidget(title)

            folder_row = QHBoxLayout()
            root_layout.addLayout(folder_row)
            folder_row.addWidget(QLabel("Source folder:"))
            self.source_edit = QLineEdit()
            folder_row.addWidget(self.source_edit, 1)
            self.browse_btn = QPushButton("Browse…")
            self.browse_btn.clicked.connect(self.choose_folder)
            folder_row.addWidget(self.browse_btn)

            profile_row = QHBoxLayout()
            root_layout.addLayout(profile_row)
            profile_row.addWidget(QLabel("Compatibility profile:"))
            self.profile_combo = QComboBox()
            for key in (PROFILE_NTFS, PROFILE_WINDOWS, PROFILE_MACOS):
                self.profile_combo.addItem(PROFILE_LABELS[key], key)
            self.profile_combo.setCurrentIndex(self.profile_combo.findData(PROFILE_WINDOWS))
            self.profile_combo.setToolTip(
                "Choose whether to check only raw NTFS storage constraints, "
                "NTFS plus normal Windows usability, or NTFS plus macOS-style usability."
            )
            self.profile_combo.currentIndexChanged.connect(self.on_profile_changed)
            profile_row.addWidget(self.profile_combo, 1)

            dest_row = QHBoxLayout()
            root_layout.addLayout(dest_row)
            self.dest_label = QLabel("Destination prefix for path-length estimate:")
            dest_row.addWidget(self.dest_label)
            self.dest_edit = QLineEdit("")
            self.dest_edit.setPlaceholderText("optional only; e.g. E:\\backup, /Volumes/Drive/backup, or blank")
            self.dest_edit.setToolTip(
                "Optional. This is not a real mount point and it does not need to exist. "
                "It is only prefixed to source-relative paths to estimate their final path length under the selected compatibility profile."
            )
            dest_row.addWidget(self.dest_edit, 1)
            self.dest_help_btn = QPushButton("?")
            self.dest_help_btn.setToolTip("Explain destination root")
            self.dest_help_btn.setFixedWidth(34)
            self.dest_help_btn.clicked.connect(self.show_destination_root_help)
            dest_row.addWidget(self.dest_help_btn)

            self.options_box = QGroupBox("Advanced scan options")
            self.options_box.setCheckable(True)
            self.options_box.setChecked(False)
            self.options_box.setToolTip("Show profile-specific path/depth/component assumptions. Defaults and visible controls change when the compatibility profile changes.")
            root_layout.addWidget(self.options_box)
            options_layout = QVBoxLayout(self.options_box)
            self.options_content = QWidget()
            options_layout.addWidget(self.options_content)
            grid = QGridLayout(self.options_content)

            self.profile_info_label = QLabel("")
            self.profile_info_label.setWordWrap(True)
            self.profile_info_label.setObjectName("ProfileInfoLabel")
            grid.addWidget(self.profile_info_label, 0, 0, 1, 4)

            self.depth_spin = QSpinBox()
            self.depth_spin.setRange(0, 999)
            self.depth_spin.setValue(12)
            self.file_limit_spin = QSpinBox()
            self.file_limit_spin.setRange(50, 32760)
            self.file_limit_spin.setValue(259)
            self.dir_limit_spin = QSpinBox()
            self.dir_limit_spin.setRange(50, 32760)
            self.dir_limit_spin.setValue(247)
            self.extended_path_spin = QSpinBox()
            self.extended_path_spin.setRange(1024, 32760)
            self.extended_path_spin.setValue(32760)
            self.comp_utf16_spin = QSpinBox()
            self.comp_utf16_spin.setRange(16, 1024)
            self.comp_utf16_spin.setValue(255)
            self.comp_utf8_spin = QSpinBox()
            self.comp_utf8_spin.setRange(16, 2048)
            self.comp_utf8_spin.setValue(255)
            self.macos_path_spin = QSpinBox()
            self.macos_path_spin.setRange(128, 32760)
            self.macos_path_spin.setValue(1024)
            self.long_paths_check = QCheckBox("Windows long paths enabled: downgrade legacy path issues to warnings")
            self.nfc_check = QCheckBox("Warn for non-NFC Unicode names")
            self.nfc_check.setChecked(True)
            self.collision_check = QCheckBox("Detect OS-style case/Unicode sibling collisions")
            self.collision_check.setChecked(True)

            self.depth_label = QLabel("Warn if relative depth >")
            self.file_limit_label = QLabel("Legacy Windows file path limit")
            self.dir_limit_label = QLabel("Legacy Windows folder path limit")
            self.extended_path_label = QLabel("NTFS extended path limit")
            self.comp_utf16_label = QLabel("NTFS component UTF-16 limit")
            self.comp_utf8_label = QLabel("CrossRename/ext4 component byte limit")
            self.macos_path_label = QLabel("macOS path byte limit")

            grid.addWidget(self.depth_label, 1, 0)
            grid.addWidget(self.depth_spin, 1, 1)
            grid.addWidget(self.extended_path_label, 1, 2)
            grid.addWidget(self.extended_path_spin, 1, 3)
            grid.addWidget(self.file_limit_label, 2, 0)
            grid.addWidget(self.file_limit_spin, 2, 1)
            grid.addWidget(self.dir_limit_label, 2, 2)
            grid.addWidget(self.dir_limit_spin, 2, 3)
            grid.addWidget(self.comp_utf16_label, 3, 0)
            grid.addWidget(self.comp_utf16_spin, 3, 1)
            grid.addWidget(self.comp_utf8_label, 3, 2)
            grid.addWidget(self.comp_utf8_spin, 3, 3)
            grid.addWidget(self.macos_path_label, 4, 0)
            grid.addWidget(self.macos_path_spin, 4, 1)
            grid.addWidget(self.long_paths_check, 5, 0, 1, 2)
            grid.addWidget(self.nfc_check, 6, 0, 1, 2)
            grid.addWidget(self.collision_check, 6, 2, 1, 2)
            self.options_content.setVisible(False)
            self.options_box.toggled.connect(self.on_options_toggled)

            action_row = QHBoxLayout()
            root_layout.addLayout(action_row)
            self.scan_btn = QPushButton("Scan")
            self.scan_btn.clicked.connect(self.start_scan)
            action_row.addWidget(self.scan_btn)
            self.cancel_btn = QPushButton("Cancel")
            self.cancel_btn.setEnabled(False)
            self.cancel_btn.clicked.connect(self.cancel_scan)
            action_row.addWidget(self.cancel_btn)
            action_row.addSpacing(18)
            self.export_csv_btn = QPushButton("Export CSV…")
            self.export_csv_btn.clicked.connect(self.export_csv)
            action_row.addWidget(self.export_csv_btn)
            self.export_json_btn = QPushButton("Export JSON…")
            self.export_json_btn.clicked.connect(self.export_json)
            action_row.addWidget(self.export_json_btn)
            action_row.addSpacing(18)
            self.rename_btn = QPushButton("Rename selected…")
            self.rename_btn.clicked.connect(self.edit_selected_name)
            action_row.addWidget(self.rename_btn)
            self.show_edited_check = QCheckBox("Show edited items")
            self.show_edited_check.setChecked(True)
            self.show_edited_check.setToolTip("Show rows for files/folders renamed inside this app since the last scan. Edited rows have a light orange background and are not counted as unresolved problems. Starting a new scan clears these rows and checks the items again.")
            self.show_edited_check.toggled.connect(lambda _checked: self.refresh_table_preserving_view())
            action_row.addWidget(self.show_edited_check)
            self.copy_paths_btn = QPushButton("Copy selected path(s)")
            self.copy_paths_btn.clicked.connect(self.copy_selected_paths)
            action_row.addWidget(self.copy_paths_btn)
            self.open_btn = QPushButton("Open selected")
            self.open_btn.clicked.connect(self.open_selected)
            action_row.addWidget(self.open_btn)
            self.reveal_btn = QPushButton("Show location")
            self.reveal_btn.clicked.connect(self.reveal_selected)
            action_row.addWidget(self.reveal_btn)
            action_row.addSpacing(18)
            self.help_btn = QPushButton("Help / About")
            self.help_btn.clicked.connect(self.show_help)
            action_row.addWidget(self.help_btn)
            action_row.addStretch(1)

            self.progress = QProgressBar()
            self.progress.setRange(0, 100)
            root_layout.addWidget(self.progress)

            self.count_label = QLabel("Problematic items: 0     ⛔ 0   🪟 0   ⚠️ 0")
            root_layout.addWidget(self.count_label)

            self.legend_label = QLabel("")
            self.legend_label.setWordWrap(True)
            root_layout.addWidget(self.legend_label)

            self.multi_problem_warning_label = QLabel(
                "Warning: Some files/folders have multiple problems. Review all problems in the bold rows."
            )
            warning_font = self.multi_problem_warning_label.font()
            warning_font.setBold(True)
            self.multi_problem_warning_label.setFont(warning_font)
            self.multi_problem_warning_label.setWordWrap(True)
            self.multi_problem_warning_label.setVisible(False)
            root_layout.addWidget(self.multi_problem_warning_label)

            self.table = IssueTableView()
            self.table.setModel(self.model)
            self.table.setSelectionBehavior(QTableView.SelectionBehavior.SelectRows)
            self.table.setSelectionMode(QTableView.SelectionMode.ExtendedSelection)
            self.table.setSortingEnabled(True)
            header = self.table.horizontalHeader()
            header.setStretchLastSection(False)
            header.setSectionsClickable(True)
            header.setSortIndicatorShown(True)
            for col in range(self.model.columnCount()):
                header.setSectionResizeMode(col, QHeaderView.ResizeMode.Interactive)
            self.table.setColumnWidth(0, 44)
            self.table.setColumnWidth(1, 170)
            self.table.setColumnWidth(2, 90)
            self.table.setColumnWidth(3, 280)
            self.table.setColumnWidth(4, 360)
            self.table.setColumnWidth(5, 460)
            self.table.setColumnWidth(6, 620)
            self.table.setWordWrap(False)
            self.table.verticalHeader().setDefaultSectionSize(28)
            self.table.verticalHeader().setVisible(False)
            self.table.setEditTriggers(QTableView.EditTrigger.EditKeyPressed)
            self.table.doubleClicked.connect(self.on_table_double_clicked)
            root_layout.addWidget(self.table, 1)

            self.setStatusBar(QStatusBar())
            self.statusBar().showMessage("Choose a folder and press Scan.")

            # Small menu for discoverability.
            file_menu = self.menuBar().addMenu("File")
            act_scan = QAction("Scan", self)
            act_scan.triggered.connect(self.start_scan)
            file_menu.addAction(act_scan)
            file_menu.addAction("Export CSV…", self.export_csv)
            file_menu.addAction("Export JSON…", self.export_json)
            file_menu.addSeparator()
            file_menu.addAction("Quit", self.close)

            edit_menu = self.menuBar().addMenu("Edit")
            act_rename = QAction("Rename selected…", self)
            act_rename.setShortcut("F2")
            act_rename.triggered.connect(self.edit_selected_name)
            edit_menu.addAction(act_rename)
            self.addAction(act_rename)

            help_menu = self.menuBar().addMenu("Help")
            help_menu.addAction("Help / About", self.show_help)

            self.on_profile_changed()


        def current_profile(self) -> str:
            data = self.profile_combo.currentData()
            return str(data or PROFILE_WINDOWS)

        def set_option_visible(self, *widgets, visible: bool) -> None:
            for widget in widgets:
                widget.setVisible(visible)

        def refresh_table_scrollbar_metrics(self) -> None:
            table = getattr(self, "table", None)
            if table is not None and hasattr(table, "refresh_scrollbar_metrics"):
                table.refresh_scrollbar_metrics()

        def edited_path_set(self) -> set[str]:
            return {record.new_absolute_path for record in self.edited_records.values()}

        def display_issues(self) -> list[Issue]:
            """Return issues plus optional scan-local edited rows for the table only."""
            show_edited = bool(getattr(self, "show_edited_check", None) is None or self.show_edited_check.isChecked())
            edited_paths = self.edited_path_set()
            visible: list[Issue] = []
            for issue in self.issues:
                if not show_edited and issue.absolute_path in edited_paths:
                    continue
                visible.append(issue)
            if show_edited:
                paths_already_visible = {issue.absolute_path for issue in visible}
                for record in self.edited_records.values():
                    if record.new_absolute_path not in paths_already_visible:
                        visible.append(edited_record_to_issue(record))
            return visible

        def refresh_table_preserving_view(self, preserve_scroll: bool = True, *, focus_path: Optional[str] = None) -> None:
            """Refresh rows, optionally restoring focus to a renamed item."""
            table = getattr(self, "table", None)
            vbar = table.verticalScrollBar() if table is not None else None
            hbar = table.horizontalScrollBar() if table is not None else None
            old_v = vbar.value() if preserve_scroll and vbar is not None else 0
            old_h = hbar.value() if preserve_scroll and hbar is not None else 0
            current_column = table.currentIndex().column() if table is not None else -1
            had_focus = table is not None and table.hasFocus()
            self.model.set_issues(self.display_issues(), self.edited_path_set())
            self.multi_problem_warning_label.setVisible(
                any(item.is_multi_problem for item in self.model.items)
            )
            self.refresh_table_scrollbar_metrics()

            if preserve_scroll and table is not None:
                def restore_view() -> None:
                    # Restore selection after Qt has finished closing the inline editor.
                    if focus_path is not None:
                        for row, item in enumerate(self.model.items):
                            if item.primary_issue.absolute_path == focus_path:
                                table.setCurrentIndex(self.model.index(
                                    row, current_column if current_column >= 0 else 3
                                ))
                                break
                        if had_focus:
                            table.setFocus(Qt.FocusReason.OtherFocusReason)
                    if vbar is not None:
                        vbar.setValue(min(old_v, vbar.maximum()))
                    if hbar is not None:
                        hbar.setValue(min(old_h, hbar.maximum()))
                    self.refresh_table_scrollbar_metrics()
                QTimer.singleShot(0, restore_view)

        def on_options_toggled(self, checked: bool) -> None:
            self.options_content.setVisible(checked)
            # Wait until Qt has completed the layout pass, then recompute the
            # table scrollbar geometry. This makes the vertical thumb resize
            # when the advanced-options panel is opened/closed.
            QTimer.singleShot(0, self.refresh_table_scrollbar_metrics)

        def apply_profile_defaults_to_options(self, profile: str) -> None:
            defaults = settings_for_profile(profile)
            self.depth_spin.setValue(defaults.max_depth)
            self.file_limit_spin.setValue(defaults.max_file_path_utf16_units)
            self.dir_limit_spin.setValue(defaults.max_dir_path_utf16_units)
            self.extended_path_spin.setValue(defaults.max_extended_path_utf16_units)
            self.comp_utf16_spin.setValue(defaults.max_component_utf16_units)
            self.comp_utf8_spin.setValue(defaults.max_component_utf8_bytes)
            self.macos_path_spin.setValue(defaults.max_macos_path_utf8_bytes)
            self.long_paths_check.setChecked(defaults.long_paths_enabled)

        def on_profile_changed(self) -> None:
            profile = self.current_profile()
            self.apply_profile_defaults_to_options(profile)
            profile_name = profile_label(profile)
            self.profile_info_label.setText(
                f"You chose: <b>{profile_name}</b>. {PROFILE_DESCRIPTIONS.get(profile, '')} "
                "The option values below are the defaults for this profile."
            )
            if profile == PROFILE_WINDOWS:
                self.dest_label.setText("Windows destination prefix for path-length estimate:")
                self.dest_edit.setPlaceholderText("optional only; e.g. E:\\backup. Leave blank for source-relative path length")
                self.comp_utf8_label.setText("CrossRename/ext4 component byte warning limit")
                os_text = "🪟 may copy to NTFS but fail in normal Windows tools"
                show_windows = True
                show_macos = False
                show_crossrename = True
            elif profile == PROFILE_MACOS:
                self.dest_label.setText("macOS destination prefix for path-length estimate:")
                self.dest_edit.setPlaceholderText("optional only; e.g. /Volumes/Drive/backup. Leave blank for source-relative path length")
                self.comp_utf8_label.setText("macOS component byte limit")
                os_text = "🍎 may copy to NTFS but fail or be awkward in normal macOS/Finder-style use"
                show_windows = False
                show_macos = True
                show_crossrename = True
            else:
                self.dest_label.setText("Optional destination prefix for NTFS path-length estimate:")
                self.dest_edit.setPlaceholderText("optional only; leave blank for source-relative path length")
                os_text = "💻 OS/application access-risk checks are disabled in pure NTFS mode"
                show_windows = False
                show_macos = False
                show_crossrename = False
            self.dest_edit.setToolTip(
                "Optional. This is not a real mount point and it does not need to exist. "
                "It is only prefixed to source-relative paths to estimate final path length under the selected profile.\n\n"
                + PROFILE_DESCRIPTIONS.get(profile, "")
            )
            self.set_option_visible(self.file_limit_label, self.file_limit_spin, self.dir_limit_label, self.dir_limit_spin, self.long_paths_check, visible=show_windows)
            self.set_option_visible(self.macos_path_label, self.macos_path_spin, visible=show_macos)
            self.set_option_visible(self.comp_utf8_label, self.comp_utf8_spin, visible=show_crossrename)
            self.collision_check.setVisible(profile in (PROFILE_WINDOWS, PROFILE_MACOS))
            self.legend_label.setText(
                f"⛔ cannot be copied as a normal NTFS object  •  {os_text}  •  ⚠️ portable-metadata/path warning\n"
                "🟧/bold row = this file/folder has more than one problem. These rows stay at the end in A-Z Problem order, independently of column sorting.\n"
                "✏️/light-orange row = renamed in this app since the last scan; toggle Show edited items to hide/show these rows. Starting a new scan clears the edited state."
            )
            if getattr(self, "summary", None):
                self.issues = []
                self.summary = {}
                self.refresh_table_preserving_view(preserve_scroll=False)
                self.statusBar().showMessage("Compatibility profile changed; scan again to refresh results.")
            self.update_count_label()

        def show_destination_root_help(self) -> None:
            profile = self.current_profile()
            QMessageBox.information(
                self,
                "Destination prefix",
                "Destination prefix is optional and is used only for path-length estimates.\n\n"
                "It does not copy anything, it does not need to exist, and it does not have to be a mounted path on this Linux machine. "
                "It is the folder prefix where you expect the scanned folder's contents to end up under the selected compatibility profile.\n\n"
                "Windows example: if you scan /home/marko/archive and later copy its contents to E:\\backup, enter E:\\backup. "
                "If you copy the whole archive folder to E:\\backup\\archive, enter E:\\backup\\archive.\n\n"
                "macOS example: if the final location would be /Volumes/External/backup/archive, enter that prefix.\n\n"
                f"Current profile: {profile_label(profile)}. {PROFILE_DESCRIPTIONS.get(profile, '')}\n\n"
                "Leave it blank if you only want to check source-relative paths. Blank is convenient on Linux, but it may underestimate path-length problems if the final destination prefix is long."
            )

        def show_help(self) -> None:
            dlg = QDialog(self)
            dlg.setWindowTitle(f"Help / About — {APP_NAME}")
            dlg.resize(820, 640)
            layout = QVBoxLayout(dlg)
            browser = QTextBrowser()
            browser.setOpenExternalLinks(True)
            browser.setHtml(f"""
                <h1>{APP_NAME}</h1>
                <p><b>Version:</b> {APP_VERSION}</p>
                <p><b>Copyright Frieder Simon.</b></p>
                <h2>What this software is for</h2>
                <p>
                NTFSafe scans a local folder before you copy it from a Linux/ext4-style
                source to an NTFS drive. You can choose whether the scan targets pure NTFS storage,
                normal Windows usability, or macOS-style usability on an NTFS transfer drive. It is a
                preflight scanner: it does not copy your data and it does not bulk-rename anything.
                </p>
                <p>
                The goal is to find files and folders that would be awkward or impossible after the
                copy: hard NTFS representation problems, Windows-only filename problems, macOS-style
                path/component problems, non-NFC Unicode names, case/Unicode sibling collisions,
                symlinks, hardlinks, special files, unreadable folders, and other portability risks.
                </p>
                <h2>Compatibility profiles</h2>
                <ul>
                  <li><b>Pure NTFS compatibility</b>: checks only what an NTFS volume can represent: valid Unicode, NTFS/POSIX-namespace forbidden characters (NUL and forward slash), NTFS component length, NTFS path range, and ordinary object constraints. Normal Linux scans cannot actually contain NUL or slash inside one filename component, but the check is explicit.</li>
                  <li><b>Windows compatibility</b>: includes pure NTFS checks, then adds Windows-forbidden characters, an explicit colon/alternate-data-stream syntax check, reserved names, trailing spaces/dots, case/Unicode collisions, and legacy MAX_PATH-style risks.</li>
                  <li><b>macOS compatibility</b>: includes pure NTFS checks, then adds macOS/Finder-style colon and leading-dot warnings, UTF-8 component/path length, and default case/normalization-collision risks. It is useful when the NTFS drive is meant to be read through macOS/Finder or a macOS NTFS driver.</li>
                </ul>
                <h2>Advanced scan options</h2>
                <p>
                The <b>Advanced scan options</b> panel is profile-aware. When you open it, it says
                <b>You chose: …</b> and shows the default limits for the selected profile.
                Switching the compatibility profile resets those advanced values to that profile's
                defaults and hides irrelevant controls.
                </p>
                <ul>
                  <li><b>Pure NTFS compatibility</b>: uses a high depth comfort threshold and shows NTFS component/path limits. The default full-path limit follows Microsoft's NTFS table value of 32,760 Unicode characters. Windows-only and macOS-only controls are hidden.</li>
                  <li><b>Windows compatibility</b>: uses conservative legacy Windows path defaults and shows the Windows long-path option.</li>
                  <li><b>macOS compatibility</b>: shows macOS byte-based component/path limits and hides the Windows long-path option.</li>
                </ul>
                <h2>Profile check audit</h2>
                <p>The profiles intentionally differ. Pure NTFS mode stays close to storage-level representation; Windows and macOS add operating-system/application usability checks.</p>
                <table border="1" cellspacing="0" cellpadding="4">
                  <tr><th>Check</th><th>Pure NTFS</th><th>Windows</th><th>macOS</th></tr>
                  <tr><td>Invalid Unicode / surrogateescape names</td><td>Yes</td><td>Yes</td><td>Yes</td></tr>
                  <tr><td>NTFS/POSIX namespace forbidden chars: NUL and forward slash</td><td>Yes*</td><td>Yes*</td><td>Yes*</td></tr>
                  <tr><td>NTFS UTF-16 component length and 32,760-character path estimate</td><td>Yes</td><td>Yes</td><td>Yes</td></tr>
                  <tr><td>Special files, symlinks, hardlinks</td><td>Yes</td><td>Yes</td><td>Yes</td></tr>
                  <tr><td>Windows forbidden characters except colon</td><td>No</td><td>Yes</td><td>No</td></tr>
                  <tr><td>Windows colon / alternate-data-stream syntax risk</td><td>No</td><td>Yes</td><td>No</td></tr>
                  <tr><td>Windows reserved device names, trailing dot/space, legacy path length</td><td>No</td><td>Yes</td><td>No</td></tr>
                  <tr><td>macOS/Finder colon and leading-dot warnings</td><td>No</td><td>No</td><td>Yes</td></tr>
                  <tr><td>macOS UTF-8 component/path byte limits</td><td>No</td><td>No</td><td>Yes</td></tr>
                  <tr><td>Case/Unicode sibling collision checks</td><td>No</td><td>Windows-style</td><td>macOS-style</td></tr>
                </table>
                <p><small>* A normal Linux source path cannot contain NUL or a literal forward slash inside one filename component, but the check is present for completeness and programmatic inputs.</small></p>
                <h2>Issue classes</h2>
                <ul>
                  <li><b>⛔ NTFS copy blocker</b>: not safely representable as an ordinary NTFS file/folder, or violates a hard component/object constraint.</li>
                  <li><b>🪟 Windows access risk</b>: may exist on NTFS, especially if created through Linux or special APIs, but normal Windows apps may fail to open, rename, delete, or display it.</li>
                  <li><b>🍎 macOS access risk</b>: may exist on NTFS, but may be too long or collision-prone for ordinary macOS/Finder-style use.</li>
                  <li><b>⚠️ Portability warning</b>: usually copyable, but semantically fragile or likely to lose meaning across systems.</li>
                </ul>
                <p>
                The summary counts <b>unique problematic files/folders</b>. If one item has several
                issues, it is counted once under its most serious class, so the three emoji counts
                add up to the total problematic-item count. The table shows one row per problematic
                file/folder; the <b>Problem</b> column lists all problems for that item. Rows marked
                with the orange <b>🟧</b> multi-problem marker are shown in bold across the whole row and pinned at the end
                in case-insensitive A-Z order by their displayed <b>Problem</b> text, independently of column sorting.
                A bold warning below the legend appears whenever multi-problem rows are visible.
                Inside a multi-problem row, intrinsic name/content problems such as illegal
                characters, reserved names, non-NFC Unicode, symlinks, hardlinks, or special-file semantics are listed before
                structural depth/path/component-length problems, so concrete rename/fix actions appear first.
                </p>
                <h2>Manual renames and edited rows</h2>
                <p>
                Select a file or folder and press <b>F2</b>, or click <b>Rename selected…</b>,
                to edit its <b>Name</b>. Press <b>Enter</b> to rename the actual source item
                in place. This is a manual, one-item operation, not a bulk sanitizer.
                The table keeps its scroll position and immediately updates the row text.
                With <b>Show edited items</b> enabled, the renamed row stays selected and
                focused, so <b>Up</b>/<b>Down</b> can select the previous/next item.
                </p>
                <p>
                Renamed rows have a <b>✏️</b> marker and a light-orange background.
                <b>Show edited items</b> hides or shows items renamed since the last scan.
                The marker does not prove that the rename fixed every problem:
                run <b>Scan</b> again to refresh the findings and counts.
                Edited-item placeholders are display-only; they are not counted as
                unresolved problems or exported as findings in CSV/JSON.
                </p>
                <p>
                Starting a new scan clears all edited markers and placeholders, whether
                you scan the same folder or a different one. Every item in the selected
                folder is checked again, including previously renamed files and folders.
                Items with no remaining findings disappear from the results. Items with
                remaining findings appear as normal problem rows without the edited
                highlight, even when <b>Show edited items</b> is unchecked.
                </p>
                <h2>Destination root</h2>
                <p>
                The <b>Destination prefix</b> field is optional and is used only for path-length
                estimates under the selected profile. It is not a Linux mount path, it does not
                need to exist, and the scanner never writes to it.
                </p>
                <p>
                If the scanned folder's contents will end up under <code>E:\backup</code>, enter
                <code>E:\backup</code>. If you will copy the whole scanned folder itself to
                <code>E:\backup\archive</code>, enter <code>E:\backup\archive</code>.
                Leaving it blank means the scanner checks source-relative path lengths only, which
                is convenient on Linux but can underestimate Windows path-length problems.
                </p>
                <h2>How this compares to CrossRename</h2>
                <p>
                CrossRename is primarily a command-line renaming/sanitization tool. Its purpose is to
                change names so that they are compatible across Linux, Windows, and macOS; it supports
                dry runs, recursive renaming, optional directory renaming, Unicode lookalike
                substitutions, symlink-loop avoidance, and byte-aware filename truncation.
                </p>
                <p>
                NTFSafe is deliberately more conservative: it inspects and reports first.
                It matches the key CrossRename-style name checks — Windows-forbidden characters,
                reserved names, trailing spaces/periods, control characters, byte-aware 255-byte
                component length, and symlink-safe traversal — but it also adds NTFS/Windows/macOS-oriented
                preflight checks such as explicit Windows colon/ADS syntax detection, macOS Finder colon
                and leading-dot warnings, estimated destination-root path length, legacy MAX_PATH risk,
                UTF-16 component length, selectable pure-NTFS/Windows/macOS profiles, NFC warnings,
                case/Unicode collision detection, hardlink warnings, and special-file detection.
                </p>
                <p>
                Use CrossRename when you want an automatic sanitizer. Use this program when you first
                want a readable report, manual inspection, exportable CSV/JSON, and one-at-a-time
                fixes from a GUI.
                </p>
                <h2>Useful controls</h2>
                <ul>
                  <li><b>F2</b> or <b>Rename selected…</b>: rename the selected source item in place. After Enter, the table preserves its scroll position and keeps the renamed row selected and focused when edited items are shown.</li>
                  <li><b>Show edited items</b>: show/hide temporary edited rows until the next scan; see Manual renames and edited rows above.</li>
                  <li><b>Hover over Location</b>: show the full absolute path only.</li>
                  <li><b>Hover over Problem</b>: show all problems for that item and their detailed explanations.</li>
                  <li><b>Click a column header</b>: sort single-problem rows lexicographically by that column; the header shows an up/down sort arrow. Multi-problem rows stay at the end in A-Z Problem order.</li>
                  <li><b>Double-click Location</b>: open the parent folder in the file browser.</li>
                  <li><b>Export CSV / Export JSON</b>: save reports for later review.</li>
                  <li><b>Open selected / Show location</b>: jump to the problematic item.</li>
                </ul>
            """)
            layout.addWidget(browser)
            buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
            buttons.rejected.connect(dlg.reject)
            layout.addWidget(buttons)
            dlg.exec()

        def settings(self) -> Settings:
            return Settings(
                compatibility_profile=self.current_profile(),
                dest_root=self.dest_edit.text(),
                max_depth=int(self.depth_spin.value()),
                max_file_path_utf16_units=int(self.file_limit_spin.value()),
                max_dir_path_utf16_units=int(self.dir_limit_spin.value()),
                max_extended_path_utf16_units=int(self.extended_path_spin.value()),
                max_component_utf16_units=int(self.comp_utf16_spin.value()),
                max_component_utf8_bytes=int(self.comp_utf8_spin.value()),
                max_macos_path_utf8_bytes=int(self.macos_path_spin.value()),
                max_macos_component_utf8_bytes=int(self.comp_utf8_spin.value()),
                long_paths_enabled=bool(self.long_paths_check.isChecked()),
                check_crossrename_rules=self.current_profile() != PROFILE_NTFS,
                check_nfc=bool(self.nfc_check.isChecked()),
                check_casefold_collisions=bool(self.collision_check.isChecked()),
            )

        def choose_folder(self) -> None:
            folder = QFileDialog.getExistingDirectory(self, "Select source folder to scan")
            if folder:
                self.source_edit.setText(folder)

        def start_scan(self) -> None:
            src = self.source_edit.text().strip()
            if not src or not os.path.isdir(src):
                QMessageBox.warning(self, APP_NAME, "Please choose an existing source folder.")
                return
            self.edited_records.clear()
            self.issues = []
            self.summary = {}
            self.refresh_table_preserving_view(preserve_scroll=False)
            self.update_count_label()
            self.refresh_table_scrollbar_metrics()
            self.progress.setRange(0, 100)
            self.progress.setValue(0)
            self.statusBar().showMessage("Counting and scanning…")
            self.scan_btn.setEnabled(False)
            self.cancel_btn.setEnabled(True)

            self.thread = QThread(self)
            worker_wrap = _ScanWorker(src, self.settings())
            self.worker = worker_wrap.obj
            self.worker.moveToThread(self.thread)
            self.thread.started.connect(self.worker.run)
            self.worker.progress.connect(self.on_progress)
            self.worker.finished.connect(self.on_finished)
            self.worker.failed.connect(self.on_failed)
            self.worker.finished.connect(self.thread.quit)
            self.worker.failed.connect(self.thread.quit)
            self.thread.finished.connect(self.thread.deleteLater)
            self.thread.start()

        def cancel_scan(self) -> None:
            if self.worker is not None:
                self.worker.cancel()
                self.statusBar().showMessage("Cancelling…")

        def on_progress(self, done: int, total: int, path: str) -> None:
            self.progress.setMaximum(max(total, 1))
            self.progress.setValue(done)
            self.statusBar().showMessage(f"Scanning {done}/{total}: {path}")

        def on_finished(self, issues: list[Issue], summary: dict) -> None:
            self.issues = issues
            self.summary = summary
            self.refresh_table_preserving_view(preserve_scroll=False)
            self.update_count_label()
            self.scan_btn.setEnabled(True)
            self.cancel_btn.setEnabled(False)
            if not issues:
                self.statusBar().showMessage("No issues found under the selected assumptions.")
            else:
                profile = summary.get("compatibility_profile", self.current_profile())
                os_icon = profile_os_icon(profile)
                self.statusBar().showMessage(
                    f"Done. Problematic items={summary.get('problematic_items', len({i.absolute_path for i in issues}))}, "
                    f"⛔ {summary.get('copy_blockers', 0)}, {os_icon} {summary.get('os_access_risks', 0)}, "
                    f"⚠️ {summary.get('portability_warnings', 0)}, table rows={self.model.rowCount()}, raw issue rows={summary.get('issue_rows', len(issues))}, entries={summary.get('total_entries', 0)}."
                )
            self.worker = None
            self.thread = None

        def on_failed(self, text: str) -> None:
            self.scan_btn.setEnabled(True)
            self.cancel_btn.setEnabled(False)
            QMessageBox.critical(self, APP_NAME, text)
            self.worker = None
            self.thread = None

        def on_table_double_clicked(self, index: QModelIndex) -> None:
            if not index.isValid():
                return
            issue = self.model.issue_at(index.row())
            if issue is None:
                return
            if index.column() == 4:
                # Double-clicking the Location column opens the parent folder in the OS file browser.
                target = issue.location if os.path.isdir(issue.location) else os.path.dirname(issue.absolute_path)
                if target:
                    QDesktopServices.openUrl(QUrl.fromLocalFile(target))
                return
            self.open_selected()

        def selected_issues(self) -> list[Issue]:
            rows = sorted({idx.row() for idx in self.table.selectionModel().selectedRows()})
            selected: list[Issue] = []
            for row in rows:
                issue = self.model.issue_at(row)
                if issue:
                    selected.append(issue)
            if not selected and self.issues:
                issue = self.model.issue_at(self.table.currentIndex().row())
                if issue:
                    selected.append(issue)
            return selected

        def update_count_label(self) -> None:
            counts = problematic_item_kind_counts(self.issues)
            problematic_items = sum(counts.values())
            profile = self.current_profile() if hasattr(self, "profile_combo") else PROFILE_WINDOWS
            os_icon = profile_os_icon(profile)
            os_count = counts[ISSUE_KIND_WINDOWS_ACCESS] + counts[ISSUE_KIND_MACOS_ACCESS]
            self.count_label.setText(
                f"Problematic items: {problematic_items}     "
                f"⛔ {counts[ISSUE_KIND_COPY_BLOCKER]}   "
                f"{os_icon} {os_count}   "
                f"⚠️ {counts[ISSUE_KIND_PORTABILITY]}"
            )

        def copy_selected_paths(self) -> None:
            selected = self.selected_issues()
            if not selected:
                QMessageBox.information(self, APP_NAME, "Select one or more rows first.")
                return
            values = [safe_display(issue.absolute_path) for issue in selected]
            QGuiApplication.clipboard().setText("\n".join(values))
            self.statusBar().showMessage(f"Copied {len(values)} path(s) to clipboard.")

        def edit_selected_name(self) -> None:
            selected_rows = sorted({idx.row() for idx in self.table.selectionModel().selectedRows()})
            row = selected_rows[0] if selected_rows else self.table.currentIndex().row()
            if row < 0 or self.model.issue_at(row) is None:
                QMessageBox.information(self, APP_NAME, "Select a row first.")
                return
            index = self.model.index(row, 3)
            self.table.setCurrentIndex(index)
            self.table.edit(index)

        def rename_issue(self, issue: Issue, new_name: str) -> bool:
            new_name = new_name.strip()
            if not new_name:
                QMessageBox.warning(self, APP_NAME, "The new name cannot be empty.")
                return False
            if "/" in new_name or "\0" in new_name:
                QMessageBox.warning(self, APP_NAME, "The new name cannot contain '/' or NUL.")
                return False
            if new_name == issue.name:
                return True
            old_abs = issue.absolute_path
            old_location = issue.location
            new_abs = os.path.join(old_location, new_name)
            if os.path.lexists(new_abs):
                QMessageBox.warning(self, APP_NAME, f"A file or folder with this name already exists:\n{safe_display(new_abs)}")
                return False
            if not os.path.lexists(old_abs):
                QMessageBox.warning(self, APP_NAME, f"The selected item no longer exists:\n{safe_display(old_abs)}")
                return False
            try:
                os.rename(old_abs, new_abs)
            except OSError as exc:
                QMessageBox.critical(self, APP_NAME, f"Rename failed:\n{type(exc).__name__}: {exc}")
                return False
            old_name = issue.name
            old_rel = issue.relative_path
            if "/" in old_rel:
                new_rel = old_rel.rsplit("/", 1)[0] + "/" + new_name
            else:
                new_rel = new_name
            object_type = issue.object_type
            self._update_issue_paths_after_rename(old_abs, new_abs, old_name, new_name)
            self._record_edited_item(EditedRecord(
                old_absolute_path=old_abs,
                new_absolute_path=new_abs,
                old_name=old_name,
                new_name=new_name,
                object_type=object_type,
                location=os.path.dirname(new_abs),
                relative_path=new_rel,
                edited_at=time.strftime("%Y-%m-%d %H:%M:%S"),
            ), old_abs=old_abs, new_abs=new_abs, old_rel=old_rel, new_rel=new_rel)
            def refresh_renamed_item() -> None:
                self.refresh_table_preserving_view(preserve_scroll=True, focus_path=new_abs)

            if self.table.state() == QAbstractItemView.State.EditingState:
                # Resetting the model during commitData invalidates the active editor.
                QTimer.singleShot(0, refresh_renamed_item)
            else:
                refresh_renamed_item()
            self.update_count_label()
            self.statusBar().showMessage("Renamed. The table kept its scroll position and focus; use Show edited items to hide/show renamed rows. Run Scan again to refresh which issues remain.")
            return True

        def _record_edited_item(self, record: EditedRecord, *, old_abs: str, new_abs: str, old_rel: str, new_rel: str) -> None:
            """Track GUI renames and keep earlier edited descendants coherent if a folder is renamed."""
            old_abs_sep = old_abs + os.sep
            old_rel_sep = old_rel + "/"
            updated: dict[str, EditedRecord] = {}
            for existing in self.edited_records.values():
                if existing.new_absolute_path == old_abs or existing.new_absolute_path.startswith(old_abs_sep):
                    existing.new_absolute_path = new_abs + existing.new_absolute_path[len(old_abs):]
                    existing.location = os.path.dirname(existing.new_absolute_path)
                    if existing.relative_path == old_rel:
                        existing.relative_path = new_rel
                    elif existing.relative_path.startswith(old_rel_sep):
                        existing.relative_path = new_rel + existing.relative_path[len(old_rel):]
                updated[existing.new_absolute_path] = existing
            updated[record.new_absolute_path] = record
            self.edited_records = updated

        def _update_issue_paths_after_rename(self, old_abs: str, new_abs: str, old_name: str, new_name: str) -> None:
            old_abs_sep = old_abs + os.sep
            # Find one representative old relative path before mutating.
            old_rel = None
            for i in self.issues:
                if i.absolute_path == old_abs:
                    old_rel = i.relative_path
                    break
            if old_rel is None:
                old_rel = old_name
            if "/" in old_rel:
                new_rel = old_rel.rsplit("/", 1)[0] + "/" + new_name
            else:
                new_rel = new_name
            old_rel_sep = old_rel + "/"

            for i in self.issues:
                if i.absolute_path == old_abs:
                    i.absolute_path = new_abs
                    i.name = new_name
                    i.location = os.path.dirname(new_abs)
                    i.relative_path = new_rel
                elif i.absolute_path.startswith(old_abs_sep):
                    i.absolute_path = new_abs + i.absolute_path[len(old_abs):]
                    if i.location == old_abs or i.location.startswith(old_abs_sep):
                        i.location = new_abs + i.location[len(old_abs):]
                    if i.relative_path.startswith(old_rel_sep):
                        i.relative_path = new_rel + i.relative_path[len(old_rel):]

        def open_selected(self) -> None:
            selected = self.selected_issues()
            if not selected:
                QMessageBox.information(self, APP_NAME, "Select a row first.")
                return
            for issue in selected[:10]:
                if os.path.exists(issue.absolute_path):
                    QDesktopServices.openUrl(QUrl.fromLocalFile(issue.absolute_path))
                else:
                    QDesktopServices.openUrl(QUrl.fromLocalFile(issue.location))

        def reveal_selected(self) -> None:
            selected = self.selected_issues()
            if not selected:
                QMessageBox.information(self, APP_NAME, "Select a row first.")
                return
            for issue in selected[:10]:
                reveal_in_file_manager(issue.absolute_path)

        def export_csv(self) -> None:
            if not self.summary:
                QMessageBox.information(self, APP_NAME, "Run a scan first.")
                return
            path, _ = QFileDialog.getSaveFileName(self, "Export CSV report", "ntfs-preflight-report.csv", "CSV files (*.csv);;All files (*)")
            if path:
                write_csv(path, self.issues, self.summary)
                self.statusBar().showMessage(f"CSV report written to {path}")

        def export_json(self) -> None:
            if not self.summary:
                QMessageBox.information(self, APP_NAME, "Run a scan first.")
                return
            path, _ = QFileDialog.getSaveFileName(self, "Export JSON report", "ntfs-preflight-report.json", "JSON files (*.json);;All files (*)")
            if path:
                write_json(path, self.issues, self.summary)
                self.statusBar().showMessage(f"JSON report written to {path}")

    app = QApplication(sys.argv[:1])
    app.setApplicationName(APP_NAME)
    try:
        app.setStyle("Fusion")
    except Exception:
        pass

    def application_stylesheet(c: dict[str, str]) -> str:
        return f"""
        QWidget {{ color: {c['text']}; }}
        QMainWindow, QDialog {{ background-color: {c['window']}; color: {c['text']}; font-size: 10.5pt; }}
        QMenuBar, QMenu {{ background-color: {c['panel']}; color: {c['text']}; }}
        QMenuBar::item:selected, QMenu::item:selected {{ background-color: {c['header_hover']}; }}
        QPushButton {{
            padding: 7px 12px;
            border-radius: 5px;
            border: 1px solid {c['border']};
            background-color: {c['button']};
            color: {c['button_text']};
        }}
        QPushButton:hover {{ background-color: {c['button_hover']}; }}
        QPushButton:disabled {{ color: {c['muted']}; background-color: {c['panel']}; }}
        QLineEdit, QSpinBox {{
            padding: 5px;
            border-radius: 4px;
            border: 1px solid {c['border']};
            background-color: {c['input']};
            color: {c['text']};
            selection-background-color: {c['selection']};
            selection-color: {c['selection_text']};
        }}
        QLineEdit:focus, QSpinBox:focus {{ background-color: {c['input_focus']}; border: 1px solid {c['selection']}; }}
        QGroupBox {{
            font-weight: 600;
            margin-top: 10px;
            border: 1px solid {c['border']};
            border-radius: 6px;
            padding-top: 8px;
            background-color: {c['panel']};
        }}
        QGroupBox::title {{ subcontrol-origin: margin; left: 8px; padding: 0 4px; background-color: {c['window']}; }}
        QProgressBar {{ min-height: 17px; text-align: center; border-radius: 4px; border: 1px solid {c['border']}; background-color: {c['panel']}; color: {c['text']}; }}
        QProgressBar::chunk {{ background-color: {c['selection']}; border-radius: 3px; }}
        QTextBrowser {{ background-color: {c['base']}; color: {c['text']}; border: 1px solid {c['border']}; }}
        QLabel#ProfileInfoLabel {{ padding: 6px; border: 1px solid {c['border']}; border-radius: 5px; background-color: {c['base']}; }}
        QStatusBar {{ background-color: {c['panel']}; color: {c['text']}; }}
    """

    window = None

    def apply_gui_theme(_scheme=None) -> None:
        c = _gui_colors()
        app.setPalette(_gui_palette(c))
        app.setStyleSheet(application_stylesheet(c))
        if window is not None:
            window.table.setStyleSheet(table_view_stylesheet(window.table))
            window.update()

    apply_gui_theme()
    window = MainWindow()
    QGuiApplication.styleHints().colorSchemeChanged.connect(apply_gui_theme)
    window.show()
    return int(app.exec())


def parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=f"{APP_NAME}: scan a folder before copying to an NTFS drive.")
    p.add_argument("folder", nargs="?", help="Folder to scan. If omitted, the PySide6 GUI opens.")
    p.add_argument("--profile", choices=[PROFILE_NTFS, PROFILE_WINDOWS, PROFILE_MACOS], default=PROFILE_WINDOWS, help="Compatibility profile: pure NTFS, Windows-on-NTFS, or macOS-style NTFS use. Default: windows")
    p.add_argument("--dest-root", default="", help="Optional destination prefix for path estimates, e.g. E:\\backup or /Volumes/External/backup. Default: blank, meaning source-relative path length only.")
    p.add_argument("--max-depth", type=int, default=None, help="Warn if relative component depth exceeds this value. Default depends on --profile.")
    p.add_argument("--file-path-limit", type=int, default=None, help="Legacy Windows file path warning/error limit in UTF-16 code units. Default depends on --profile.")
    p.add_argument("--dir-path-limit", type=int, default=None, help="Legacy Windows directory path warning/error limit in UTF-16 code units. Default depends on --profile.")
    p.add_argument("--component-utf16-limit", type=int, default=None, help="NTFS component limit in UTF-16 code units. Default depends on --profile.")
    p.add_argument("--component-utf8-byte-limit", type=int, default=None, help="Byte-aware component limit used by CrossRename/ext4/macOS-style checks. Default depends on --profile.")
    p.add_argument("--macos-path-byte-limit", type=int, default=None, help="macOS-style full path byte limit used in the macos profile. Default depends on --profile.")
    p.add_argument("--ntfs-extended-path-limit", type=int, default=None, help="Pure NTFS extended path limit in UTF-16 code units. Default depends on --profile.")
    p.add_argument("--long-paths-enabled", action="store_true", help="Downgrade legacy Windows path-length errors to warnings.")
    p.add_argument("--no-nfc-check", action="store_true", help="Disable warnings for non-NFC Unicode names.")
    p.add_argument("--no-collision-check", action="store_true", help="Disable OS-style case/Unicode collision detection in Windows/macOS profiles.")
    p.add_argument("--csv", help="Write full CSV report to this path.")
    p.add_argument("--json", help="Write full JSON report to this path.")
    return p.parse_args(argv)


def main(argv: Optional[list[str]] = None) -> int:
    ns = parse_args(sys.argv[1:] if argv is None else argv)
    if not ns.folder:
        return run_gui()

    if not os.path.isdir(ns.folder):
        print(f"Not a folder: {ns.folder}", file=sys.stderr)
        return 2

    settings = settings_for_profile(ns.profile)
    settings.dest_root = ns.dest_root
    if ns.max_depth is not None:
        settings.max_depth = ns.max_depth
    if ns.file_path_limit is not None:
        settings.max_file_path_utf16_units = ns.file_path_limit
    if ns.dir_path_limit is not None:
        settings.max_dir_path_utf16_units = ns.dir_path_limit
    if ns.ntfs_extended_path_limit is not None:
        settings.max_extended_path_utf16_units = ns.ntfs_extended_path_limit
    if ns.component_utf16_limit is not None:
        settings.max_component_utf16_units = ns.component_utf16_limit
    if ns.component_utf8_byte_limit is not None:
        settings.max_component_utf8_bytes = ns.component_utf8_byte_limit
        settings.max_macos_component_utf8_bytes = ns.component_utf8_byte_limit
    if ns.macos_path_byte_limit is not None:
        settings.max_macos_path_utf8_bytes = ns.macos_path_byte_limit
    settings.long_paths_enabled = ns.long_paths_enabled
    settings.check_nfc = not ns.no_nfc_check
    settings.check_casefold_collisions = not ns.no_collision_check

    def progress(done: int, total: int, path: str) -> None:
        if total and (done % 500 == 0 or done == total):
            print(f"Scanning {done}/{total}: {path}", file=sys.stderr)

    issues, summary = scan_tree(ns.folder, settings, progress)
    print_summary(issues, summary)
    if ns.csv:
        write_csv(ns.csv, issues, summary)
        print(f"CSV report written to {ns.csv}")
    if ns.json:
        write_json(ns.json, issues, summary)
        print(f"JSON report written to {ns.json}")

    return 1 if summary.get("errors", 0) else 0


if __name__ == "__main__":
    raise SystemExit(main())
