# NTFSafe

[![Codacy Badge](https://app.codacy.com/project/badge/Grade/ba6135b4182245519c04d12bb376a0d3)](https://app.codacy.com/gh/friederrr/ntfsafe/dashboard?utm_source=gh&utm_medium=referral&utm_content=&utm_campaign=Badge_grade)
[![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)
[![Tests](https://img.shields.io/github/actions/workflow/status/friederrr/ntfsafe/quality.yml?label=tests)](https://github.com/friederrr/ntfsafe/actions/workflows/quality.yml)

## Table of contents

- [At-a-glance comparison](#at-a-glance-comparison)
- [Drives and filesystems you can scan](#drives-and-filesystems-you-can-scan)
- [Scan speed](#scan-speed)
- [Quick start with uv](#quick-start-with-uv)
- [Project layout](#project-layout)
- [Compatibility profiles](#compatibility-profiles)
- [GUI usage](#gui-usage)
- [Destination prefix for path-length estimate](#destination-prefix-for-path-length-estimate)
- [Issue classes and counts](#issue-classes-and-counts)
  - [Ordering of multiple problems in one row](#ordering-of-multiple-problems-in-one-row)
  - [Manual renames and edited rows](#manual-renames-and-edited-rows)
- [What the scanner checks](#what-the-scanner-checks)
  - [Pure NTFS checks](#pure-ntfs-checks)
  - [Windows-profile checks](#windows-profile-checks)
  - [macOS-profile checks](#macos-profile-checks)
  - [Cross-platform portability checks](#cross-platform-portability-checks)
  - [Profile check audit](#profile-check-audit)
- [Renaming from the GUI](#renaming-from-the-gui)
- [Open/show behavior](#openshow-behavior)
- [Wayland / Qt warning](#wayland--qt-warning)
- [CLI usage](#cli-usage)
- [Distribution](#distribution)
- [What it does not check](#what-it-does-not-check)
- [Comparison with CrossRename and ntfs-3g](#comparison-with-crossrename-and-ntfs-3g)
  - [Safety checks: NTFSafe vs CrossRename vs ntfs-3g](#safety-checks-ntfsafe-vs-crossrename-vs-ntfs-3g)
- [GUI appearance](#gui-appearance)
- [Development checks](#development-checks)
- [References](#references)

## At-a-glance comparison

| Check / feature | NTFSafe | CrossRename | ntfs-3g |
|---|:---:|:---:|:---:|
| Windows-invalid characters, device names, trailing spaces/dots | ✅ | ✅ | ✅ |
| Explicit NTFS filename-length check (UTF-16) | ✅ | ❌ | ✅ |
| UTF-8 filename byte-limit check | ✅ | ✅ | ❌ |
| Destination-aware Windows, NTFS, and macOS full-path estimates | ✅ | ❌ | ❌ |
| Excessive nesting warnings | ✅ | ❌ | ❌ |
| Case-insensitive and Unicode-normalization sibling-collision warnings | ✅ | ❌ | ❌ |
| Source-name Unicode validity and NFC diagnostics | ✅ | ❌ | ❌ |
| Symlink and hardlink copy-semantics warnings | ✅ | ❌ | ❌ |
| Special-file copy blockers and unreadable-source scan errors | ✅ | ❌ | ❌ |
| Classified GUI/CLI reports with CSV/JSON export | ✅ | ❌ | ❌ |

NTFSafe checks use the relevant compatibility profile. CrossRename sanitizes names; ntfs-3g enforces constraints during creation, not as a source-tree preflight. **The ntfs-3g Windows-name check requires the optional `windows_names` mount setting**, which is not its default.

See the [full comparison with CrossRename and ntfs-3g](#comparison-with-crossrename-and-ntfs-3g) for details, partial coverage, and caveats.

NTFSafe is a small Python/PySide6 preflight scanner for checking whether a local folder tree can be copied to an NTFS drive without later discovering that some files/folders are unusable on the intended operating system (Windows on NTFS, or Mac on NTFS). It

- offers different sets of checks depending on what your target is. It can operate both in GUI mode and in CLI mode.
- is read-first and conservative.
-  does **not** copy files, does **not** bulk-rename files, and does **not** modify file contents. The GUI has one manual convenience feature: you can select a found problematic item and press **F2** or **Rename selected** to rename that one source file/folder in place.

## Drives and filesystems you can scan

**ext4 is not required.** NTFSafe uses standard directory-listing and metadata APIs, not ext4-specific operations. It scans readable folders on filesystems that your operating system and filesystem driver expose as ordinary paths, including:

- **Internal and external drives**, such as HDDs, SSDs, and USB drives, using ext4, NTFS, exFAT, FAT32, Btrfs, XFS, ZFS, or other filesystems supported by the host.
- **Files already stored on NTFS**, not just files awaiting an ext4-to-NTFS copy.
- **Mounted network shares**, such as SMB or NFS, when accessible through an ordinary filesystem path. Performance and available metadata depend on the network and mount implementation.

The **source filesystem** and **compatibility profile** are separate choices. The source is where the files currently reside; the profile selects the rules for their intended use on NTFS, through Windows or macOS, or at the pure NTFS level. Selecting the Windows profile does not require a Windows-formatted source drive.

Scanning an existing NTFS tree can still be useful: a permissive Linux NTFS mount may have allowed names such as `CON.txt` or `a?b.txt` that ordinary Windows applications cannot handle. The Windows profile can flag those names, path-length risks, and potential sibling-name collisions.

NTFSafe scans **mounted folders, not raw drives, partitions, or unmounted disk images**. Its assessment is limited to the names, entries, and metadata exposed by the driver and accessible with your permissions; it cannot inspect names that the mount hides or recover their original form if the driver translates them. This capability is not a guarantee that every filesystem/driver combination has been individually tested, nor a raw-filesystem, alternate-data-stream, or permissions audit.

## Scan speed

**Even terabytes of data can be scanned in seconds when stored in a modest number of files.** NTFSafe inspects filenames, paths, and filesystem metadata without reading file contents, so it does not need to read through those terabytes.

Actual scan time depends primarily on the number of files and folders and filesystem/storage latency, rather than the total data size. Millions of small files or slow storage can take much longer; seconds are not a timing guarantee. This is a compatibility scan, not a file-content or copy-integrity verification.

## Quick start with uv

The intended way to run the tool is:

```bash
# Starts the GUI
uv run --script src/ntfsafe.py
```

To use the command-line interface, pass the folder to scan:

```bash
# Scans the folder and prints the report in the terminal
uv run --script src/ntfsafe.py /path/to/folder
```

The GUI opens when no folder is supplied. Supplying a folder runs the CLI scan instead; see the [CLI usage](#cli-usage) section for options and report exports.

The script contains inline dependency metadata, so `uv` creates or reuses an isolated environment and installs PySide6 automatically.

If PySide6 is still not found, use the explicit dependency fallback:

```bash
uv run --with 'PySide6>=6.7,<7' python src/ntfsafe.py
```

Avoid this form for the standalone script unless you are deliberately running from a project environment that already has PySide6:

```bash
uv run python src/ntfsafe.py
```

That launches `python` as the command and may not cause uv to read the script's inline dependency metadata. The usual symptom is:

```text
PySide6 is required for the GUI.
Import error: No module named 'PySide6'
```

## Project layout

```text
.
├── .github/
│   └── workflows/
│       └── quality.yml
├── .gitignore
├── .pre-commit-config.yaml
├── README.md
├── pyproject.toml
├── uv.lock
├── src/
│   └── ntfsafe.py
└── tests/
    └── test_gui_results.py
```

The application has one canonical Python source file in `src/`; project metadata and development tooling stay at the repository root. The two former uv launchers performed the same action as the direct `uv run --script src/ntfsafe.py` command, so they were removed rather than maintaining duplicate wrappers. A separate `requirements.txt` is unnecessary because dependencies are declared in `pyproject.toml` and the standalone script metadata.

## Compatibility profiles

The scanner has three selectable profiles. Each profile answers a slightly different question.

| Profile | Question it answers | What it adds |
|---|---|---|
| **Pure NTFS compatibility** | “Can an NTFS filesystem represent this as an ordinary file/folder tree?” | NTFS UTF-16 component length, the 32,760-character NTFS path estimate, Unicode validity, the explicit NTFS/POSIX-namespace NUL/slash constraint, and special-file/object checks. It deliberately ignores Windows/Win32 namespace restrictions such as `CON` or `?`. At the raw NTFS/POSIX-namespace level those names can exist, but ordinary Windows tools cannot handle them. If by “NTFS compatibility” you mean “an NTFS drive that Windows should use normally,” choose the Windows profile instead. |
| **Windows compatibility** | “Can this be copied to NTFS and then used normally from Windows?” | Everything in pure NTFS, plus Windows-forbidden characters, an explicit colon/NTFS alternate-data-stream syntax check, reserved names, trailing spaces/dots, legacy `MAX_PATH`-style path risks, and Windows-style case/Unicode collisions. |
| **macOS compatibility** | “Can this be copied to NTFS and then used reasonably through macOS/Finder or a macOS NTFS driver?” | Everything in pure NTFS, plus macOS/Finder-style colon and leading-dot warnings, UTF-8 component/path byte limits, and default case/Unicode-collision checks. |

The profile selection matters most for paths/names that are valid at the NTFS filesystem level but fragile for one operating system. For example, `CON.txt`, `a:b.txt`, and `a?b.txt` are Windows usability/API problems, not pure NTFS storage-size problems; a colon also matters for macOS/Finder-style workflows; and a very long UTF-8 path may be more relevant to macOS-style API limits than to raw NTFS storage.

The **Advanced scan options** panel is profile-aware:

| Profile | Default depth warning | Visible profile-specific options |
|---|---:|---|
| Pure NTFS compatibility | `> 999` | NTFS component UTF-16 limit and the 32,760-character NTFS path limit. Windows/macOS-only controls are hidden. |
| Windows compatibility | `> 12` | Legacy Windows file/folder path limits, NTFS limits, CrossRename/ext4 byte warning limit, and **Windows long paths enabled**. |
| macOS compatibility | `> 48` | NTFS limits, macOS component byte limit, and macOS full-path byte limit. Windows long-path controls are hidden. |

Those depth numbers are comfort warnings, not hard filesystem nesting limits. Real failures usually come from total path length, component length, OS APIs, or application behavior.

## GUI usage

Run:

```bash
uv run --script src/ntfsafe.py
```

Then:

1. Choose the source folder to scan.
2. Choose the **Compatibility profile**: pure NTFS, Windows, or macOS.
3. Leave **Destination prefix for path-length estimate** blank unless you know where the tree will finally live.
4. Open **Advanced scan options** only if you want to inspect or change the profile-specific depth/path/component assumptions. The panel explicitly shows the selected profile and changes/hides controls depending on whether you chose pure NTFS, Windows, or macOS compatibility.
5. Click **Scan**.
6. Review the scrollable table of problematic files and folders.
7. Use **Export CSV…** or **Export JSON…** to save a report.

The table behaves like a simple file-manager list view:

- columns can be resized by dragging the header separators;
- horizontal and vertical scrollbars are high-contrast and larger than the default Qt scrollbars;
- the vertical scrollbar thumb is dynamic: it shrinks/grows with the number of rows, the window height, and whether **Advanced scan options** is expanded;
- horizontal and vertical scrolling use per-pixel scrolling;
- hover over the **Location** column to see only the full absolute path;
- hover over the **Problem** column to see all problems for that item plus their detailed explanations;
- hover over other truncated cells to see the full value;
- click a column header to sort single-problem rows lexicographically by that column; the header shows an up/down sort arrow;
- rows marked with the orange **🟧** multi-problem marker have more than one problem; after scanning, they are automatically sorted A–Z (case-insensitively) by their displayed **Problem** text and pinned at the end, independently of column sorting; inside those rows, intrinsic name/content problems are listed before path-depth, nesting, and filename/path-length problems;
- a bold warning below the icon/color legend appears whenever multi-problem rows are visible, reminding you to review every problem in those rows;
- double-click the **Location** cell to open the parent folder in the file browser;
- select a row and press **F2** to rename the problematic file/folder in place; after you press Enter, the table preserves its scroll position and, when **Show edited items** is enabled, keeps the renamed row selected and focused so you can immediately use **Up/Down** to navigate;
- use **Show edited items** to hide/show rows for files/folders renamed since the last scan; edited rows are marked with ✏️ and a light-orange background and are not counted as unresolved problems; starting a new scan clears the edited state and checks every item again;
- use **Open selected** or **Show location** to jump to the item;
- use **Help / About** for an in-app explanation and comparison with CrossRename.

The GUI follows Qt's system color scheme, including Ubuntu's light/dark appearance setting. It applies matching colors to the application palette as well as the custom controls, and updates while the app is open when Qt reports a system color-scheme change. If the desktop does not expose a color-scheme setting to Qt, the GUI falls back to the current Qt palette.

## Destination prefix for path-length estimate

The **Destination prefix for path-length estimate** field is blank by default.

This field is often misunderstood. It is **not** a Linux mount path and it does **not** copy, open, or write anything. It also does **not** need to exist. It is only a text prefix used to estimate the eventual path length under the selected compatibility profile.

Blank means:

```text
Estimate path length using only the source-relative path.
```

This is convenient when you run the scanner on Linux and do not yet know whether the NTFS drive will eventually appear as `E:\`, `D:\backup`, `/Volumes/External/backup`, or something else.

Examples:

| Copy plan | Destination prefix to enter |
|---|---|
| Copy the scanned folder's **contents** directly to the root of a Windows-visible drive | `E:\` |
| Copy the scanned folder's **contents** to a Windows backup folder | `E:\backup` |
| Copy the scanned folder's **contents** to Windows Documents | `C:\Users\Marko\Documents\backup` |
| Copy the **whole scanned folder** named `archive` into `E:\backup` | `E:\backup\archive` |
| Copy/read the tree through macOS under an external volume | `/Volumes/External/backup/archive` |

Why this matters:

```text
source-relative path:        nested/nested/very-long-name.txt
destination prefix:          E:\backup\archive
estimated Windows path:      E:\backup\archive\nested\nested\very-long-name.txt
```

A path that is safe source-relative may become unsafe under a long destination prefix. Leaving the field blank is fine for a first pass, but it can underestimate path-length problems.

## Issue classes and counts

The GUI shows a compact summary like:

```text
Problematic items: N     ⛔ A   🪟/🍎/💻 B   ⚠️ C
```

`Problematic items` counts unique files/folders with at least one finding. The emoji numbers also count unique files/folders, not issue rows. If one item has multiple findings, it is counted once under its most serious class, so `A + B + C = N`. The table shows **one row per problematic file/folder**; the **Problem** column lists all problems found for that item. Rows marked with the orange **🟧** marker have more than one problem and are bold across the entire row. Those multi-problem rows are automatically sorted A–Z (case-insensitively) by their displayed **Problem** text and kept at the end of the table. Column-header sorting only reorders single-problem rows; the multi-problem group retains its A–Z Problem order. A bold warning below the legend appears whenever multi-problem rows are visible. Within a multi-problem row, intrinsic name/content problems are listed before structural depth/path/component-length problems. For example, if a file has both an illegal Windows character and a too-long filename/path, the illegal-character problem appears first.

| Icon | Class | Meaning |
|---|---|---|
| ⛔ | NTFS copy blocker | The item is not safely representable as an ordinary NTFS file/folder, or it violates a hard component/object constraint. |
| 🪟 | Windows access risk | The item may exist on NTFS, especially if created via Linux or special APIs, but ordinary Windows tools may fail to open, rename, delete, or display it correctly. |
| 🍎 | macOS access risk | The item may exist on NTFS, but may be too long or collision-prone for ordinary macOS/Finder-style use. |
| 💻 | OS access risk disabled | In pure NTFS mode, OS/application checks are intentionally disabled, so this count should normally remain zero. |
| ⚠️ | Portability warning | The item can usually be copied, but something about it is semantically fragile or may cause future portability/sync problems. |
| 🟧 | Multiple problems marker | This file/folder has more than one independent problem. Multi-problem rows are pinned at the end in case-insensitive A–Z Problem order, independently of column sorting. |
| ✏️ | Edited item marker | This file/folder was renamed from inside the app since the last scan. Edited rows have a light-orange background and can be hidden or shown with **Show edited items**. They are not counted as unresolved compatibility problems. Starting a new scan clears the edited state. |

The explanations are shown directly below the summary in the GUI.

### Ordering of multiple problems in one row

When one file or folder has more than one finding, the **Problem** column lists all findings in a deliberate order:

1. intrinsic name/content/metadata problems first, such as illegal characters, reserved names, non-NFC Unicode, symlinks, hardlinks, special files, or collisions;
2. structural length/depth problems afterwards, such as relative depth, full path length, macOS path byte length, or NTFS/component-name length.

This means a row like `bad?name-with-a-very-long-path.txt` will show the illegal-character problem before the length/path problem, because the illegal character is usually the more concrete rename/fix action.

### Manual renames and edited rows

You can rename one selected source file/folder directly from the table by selecting the row and pressing **F2**, or by clicking **Rename selected…**. This opens the **Name** column for editing; press **Enter** to confirm the new name. The rename is performed on the actual filesystem item, using the operating system's normal rename operation. This is a manual, one-item operation, not a bulk sanitizer.

After you press Enter, the table refreshes without intentionally scrolling back to the top: the vertical and horizontal scrollbars are restored to their previous positions. The renamed row is marked with a **✏️** icon and a light-orange background.

The **Show edited items** checkbox controls whether rows for items renamed since the last scan are visible. This is deliberately independent of whether your edit actually solved the compatibility issue. Until you rescan, hiding edited items removes them from the table; toggling the checkbox back on shows them again. The marker does not prove that the rename fixed every problem: run **Scan** again to refresh the findings and counts. Edited-item placeholders are display-only; they are not included in the problematic-item count and are not exported as unresolved findings in CSV/JSON.

Starting a new scan clears all edited-item markers and placeholders, whether you scan the same folder or a different one. Every item in the selected folder, including previously renamed items, is checked again. Items with no remaining findings disappear from the results; items with remaining findings appear as normal problem rows without the edited highlight, even if **Show edited items** is unchecked.

## What the scanner checks

The selected compatibility profile controls which checks are active.

### Pure NTFS checks

I audited the pure-NTFS profile against the storage-level checklist. The active hard checks are: Unicode validity, NUL/slash as the only NTFS/POSIX-namespace forbidden filename characters, 255 UTF-16-code-unit component length, 32,760-character estimated NTFS path length, unreadable folders, and non-regular filesystem objects. Symlinks and hardlinks are warnings because NTFS can represent related concepts, but a normal copy may not preserve the same semantics.

| Check | Why it matters | Classification |
|---|---|---|
| Invalid non-Unicode filename bytes | Linux can store arbitrary filename bytes; ordinary NTFS APIs expect Unicode names. | ⛔ NTFS copy blocker |
| NUL or forward slash inside one filename component | The most permissive NTFS/POSIX filename namespace allows Unicode except NUL and `/`. A normal Linux source scan cannot actually contain either inside one component, but the check is present for completeness and programmatic/test inputs. | ⛔ NTFS copy blocker |
| NTFS component length | NTFS/Windows commonly allow up to 255 UTF-16 code units per path component. | ⛔ NTFS copy blocker |
| Estimated path exceeds NTFS 32,760-character path range | NTFS supports very long paths, but not infinite paths; Microsoft’s NTFS table lists 32,760 Unicode characters with each component no more than 255 characters. | ⛔ NTFS copy blocker |
| Special object | Sockets, FIFOs, device nodes, and other non-regular objects are not ordinary NTFS files/folders. | ⛔ NTFS copy blocker |
| Folder cannot be read | The scanner cannot know whether children are safe. | ⛔ NTFS copy blocker |

### Windows-profile checks

| Check | Why it matters | Classification |
|---|---|---|
| Windows-forbidden characters except colon | Windows normal file APIs reject reserved characters such as less-than, greater-than, quote, slash, backslash, vertical bar, question mark, asterisk, and control characters. The scanner treats colon separately because it has special drive/stream syntax meaning. This is intentionally a Windows-profile check, not a pure-NTFS check: Microsoft documents these as Windows naming rules, and Microsoft support notes that some invalid/reserved Win32 names are not NTFS limitations. | 🪟 Windows access risk |
| Colon / ADS syntax | `:` is invalid/ambiguous in ordinary Windows names because Windows uses it for drive-letter and NTFS alternate-data-stream syntax, such as `file.txt:Zone.Identifier`. | 🪟 Windows access risk |
| Trailing space or trailing dot | NTFS may store them, but ordinary Windows shell/Win32 paths handle them badly. | 🪟 Windows access risk |
| Reserved Windows device names | Names such as `CON`, `PRN`, `AUX`, `NUL`, `COM1`-`COM9`, and `LPT1`-`LPT9` are reserved even with extensions. | 🪟 Windows access risk |
| Estimated path exceeds legacy Windows file/folder limits | Many normal Windows tools still fail around `MAX_PATH` unless long-path support and app support are both present. | 🪟 Windows access risk |
| Case/Unicode-normalization sibling collision | Linux may allow `Report.txt`, `report.txt`, `Müller.txt`, and `Müller.txt` in one folder. Windows and many tools may conflate them. | 🪟 Windows access risk |

### macOS-profile checks

| Check | Why it matters | Classification |
|---|---|---|
| Component exceeds configured UTF-8 byte limit | Common macOS path APIs expose a 255-byte-ish component limit. This matters most for CJK/emoji-heavy names. | 🍎 macOS access risk |
| Estimated path exceeds configured macOS byte limit | Common macOS path APIs expose a roughly 1024-byte `PATH_MAX`-style practical limit. | 🍎 macOS access risk |
| Case/Unicode-normalization sibling collision | Default macOS/Finder/APFS-style use is usually case-insensitive, so Linux-distinct names may collide. | 🍎 macOS access risk |
| Colon in name | macOS/Finder and HFS-era path conventions treat `:` specially; some tools translate, display, or reject it. | 🍎 macOS access risk |
| Leading dot name | Dot-prefixed names are valid POSIX names, but they are hidden/awkward in Finder workflows and Finder rename UI may reject creating them. | 🍎 macOS access risk |

### Cross-platform portability checks

| Check | Why it matters | Classification |
|---|---|---|
| Relative depth above configured comfort threshold | Real filesystems usually limit total path length, not a fixed number of folder levels, but extreme depth is operationally fragile. | ⚠️ Portability warning |
| Component exceeds configured UTF-8 byte limit in Windows mode | Mirrors CrossRename's byte-aware Linux/ext4/btrfs-style check. This is intentionally disabled in pure NTFS mode because it is not a raw NTFS limitation. | ⚠️ Portability warning |
| Non-NFC Unicode name | Names can look identical while being different Unicode sequences. NFC is safer for round trips. | ⚠️ Portability warning |
| Symlink encountered | A normal copy may skip it, follow it, copy the target, or create a Windows/macOS link with different semantics. | ⚠️ Portability warning |
| File has multiple hardlinks | Normal copies often duplicate content rather than preserving hardlink identity. | ⚠️ Portability warning |

The scanner uses a conservative sibling-collision approximation: `NFC + casefold` within each directory. It is not a complete Windows or macOS collation emulator, but it catches the common dangerous cases.

### Profile check audit

The three profiles now intentionally differ as follows:

| Check | Pure NTFS | Windows | macOS |
|---|---:|---:|---:|
| Invalid Unicode / surrogateescape names | Yes | Yes | Yes |
| NTFS/POSIX namespace NUL/slash constraint | Yes* | Yes* | Yes* |
| NTFS component UTF-16 length | Yes | Yes | Yes |
| NTFS 32,760-character path estimate | Yes | Yes | Yes |
| Special/non-regular filesystem objects | Yes | Yes | Yes |
| Symlink and hardlink portability warnings | Yes | Yes | Yes |
| Windows/Win32 forbidden characters except colon, including `?` | No | Yes | No |
| Windows colon / ADS syntax risk | No | Yes | No |
| Windows reserved device names | No | Yes | No |
| Windows trailing space/dot risk | No | Yes | No |
| Windows legacy file/folder path length | No | Yes | No |
| Windows-style case/Unicode collisions | No | Yes | No |
| macOS colon/Finder risk | No | No | Yes |
| macOS leading-dot/Finder warning | No | No | Yes |
| macOS component/path UTF-8 byte limits | No | No | Yes |
| macOS-style case/Unicode collisions | No | No | Yes |
| CrossRename/ext4 UTF-8 byte warning | No | Yes | Covered by macOS byte checks |

\* A normal Linux source path cannot contain NUL or a literal slash inside one filename component; this check is mainly for completeness and programmatic inputs.


## Renaming from the GUI

Select a row and press:

```text
F2
```

or click:

```text
Rename selected…
```

This edits the **Name** column. When you confirm the edit, the tool renames the source file/folder in place using the operating system's normal rename operation. The table preserves its scroll position after the rename instead of jumping back to the top.

After renaming, run **Scan** again to refresh the issue list. The row text is updated immediately and the row is marked with **✏️** plus a light-orange background, but the scanner does not automatically prove that the new name fixed every issue until you rescan. Use **Show edited items** to hide/show items renamed since the last scan. Starting a new scan clears these markers and checks the renamed items again.

This is meant for manual fixing. It is not a bulk sanitizer.

## Open/show behavior

For portable opening, the GUI uses Qt's `QDesktopServices.openUrl(QUrl.fromLocalFile(...))`. This asks the operating system to open a local file URL with the associated application or file manager.

For **Show location**, the tool uses best-effort platform-specific helpers:

| OS | Method |
|---|---|
| Windows | `explorer /select,` |
| macOS | `/usr/bin/open -R` (absolute system path; no `PATH` lookup) |
| Linux | DBus `org.freedesktop.FileManager1.ShowItems` when available, otherwise opens the parent folder |

If **Show location** raises an exception, it logs a warning with the path and traceback (to stderr by default) and returns `False`, without displaying an additional dialog.

## Wayland / Qt warning

On some Linux Wayland sessions, Qt/PySide6 may print messages like:

```text
qt.qpa.wayland.textinput: ... Trying to enable ... but ... is already enabled
qt.qpa.wayland.textinput: ... Trying to disable ... but ... is focused
```

The tool does **not** suppress these messages. They are usually Qt/Wayland text-input warnings, not scanner errors. If the GUI itself behaves badly on Wayland, you can try running through XWayland:

```bash
QT_QPA_PLATFORM=xcb uv run --script src/ntfsafe.py
```

## CLI usage

The same scanner can be run from the command line:

```bash
uv run --script src/ntfsafe.py /path/to/source \
  --profile windows \
  --csv report.csv \
  --json report.json
```

Profiles:

```bash
--profile ntfs
--profile windows
--profile macos
```

If you know the final destination prefix, include it:

```bash
uv run --script src/ntfsafe.py /path/to/source \
  --profile windows \
  --dest-root 'E:\backup' \
  --csv report.csv \
  --json report.json
```

macOS-style example:

```bash
uv run --script src/ntfsafe.py /path/to/source \
  --profile macos \
  --dest-root '/Volumes/External/backup' \
  --csv report.csv
```

Most CLI limits default from the selected profile. For example, `--profile ntfs` uses a much higher depth warning and hides/ignores Windows-only long-path assumptions in the GUI, while `--profile windows` uses conservative legacy Windows path defaults.

View all CLI options:

```bash
uv run --script src/ntfsafe.py --help
```

The command exits with status code `1` if error-level issues are found, otherwise `0`.

## Distribution

This repository currently provides the source application only. AppImage and other binary packaging are not included.

## What it does not check

This is a preflight scanner, not a copier, filesystem checker, or backup verifier. It does not:

- copy files;
- bulk-rename or sanitize files;
- verify file contents by hash;
- run `chkdsk`;
- inspect the actual Windows long-path policy on the destination machine;
- guarantee behavior of every Windows or macOS application;
- preserve or restore creation times, permissions, ACLs, xattrs, or symlink semantics.

For an actual important copy, use a deliberate copy command such as:

```bash
rsync -rt --info=progress2 /source/ /mnt/ntfs/target/
```

Then verify contents and mtimes with:

```bash
rsync -rtcn --itemize-changes /source/ /mnt/ntfs/target/
```

## Comparison with CrossRename and ntfs-3g

### Safety checks: NTFSafe vs CrossRename vs ntfs-3g

**For an ext4-to-NTFS copy intended for Windows, use NTFSafe's Windows profile.** It covers the common Windows naming hazards targeted by CrossRename, and adds preflight checks for destination path lengths, sibling-name collisions, Unicode normalization, and copy-semantics risks. A successful copy through ntfs-3g alone does **not** establish that ordinary Windows applications can use the result.

Here, **the old stable NTFS driver means ntfs-3g**, not the newer kernel `ntfs3` driver or the historical, limited-write kernel `ntfs` driver. The comparison uses [CrossRename's implementation](https://github.com/Jemeni11/CrossRename/blob/main/src/crossrename/rename.py) (version 1.6.0) and the [ntfs-3g 2022.10.3 manual](https://manpages.debian.org/bookworm/ntfs-3g/ntfs-3g.8.en.html).

**How to read the table:** NTFSafe reports problems before copying; CrossRename sanitizes names (or previews renames with `--dry-run`); ntfs-3g enforces constraints only when an operation reaches the mounted destination. **Only with `windows_names`** means an optional ntfs-3g mount setting, not its default behavior. Mount helpers or distributions may enable it for you; check the actual mount options.

| Safety check / feature | NTFSafe | CrossRename | Old stable NTFS driver: ntfs-3g |
|---|---|---|---|
| Windows-forbidden punctuation, such as `?`, `*`, `<`, `>`, and backslash | **Yes**, Windows profile. | **Yes**, sanitizes it. | **Only with `windows_names`**; otherwise permits these POSIX-namespace names. |
| Colon / Windows alternate-data-stream syntax risk | **Yes**, explicit Windows-profile issue. | **Yes**, removes or replaces colons. | **Only with `windows_names`** rejects colons in ordinary names; stream handling also depends on `streams_interface`. |
| Windows-forbidden control characters, U+0001-U+001F | **Yes**, Windows profile. | **Yes**, removes them. | **Only with `windows_names`**. |
| Common Windows device names, including extensions (`CON.txt`, `NUL`, `COM1`, etc.) | **Yes**, Windows profile; also includes superscript-digit COM/LPT variants. | **Yes**, including superscript-digit COM/LPT variants. | **Only with `windows_names`** for the reserved names documented in its manual. |
| Trailing spaces or dots | **Yes**, Windows profile. | **Yes**, strips them. | **Only with `windows_names`**. |
| Invalid filename bytes that cannot be decoded as Unicode | **Yes**, reports Linux surrogateescape names in every profile. | **No dedicated diagnostic**; its UTF-8 encoding step can fail instead. | **Rejects unconvertible names during creation**, according to its configured encoding; not a source-tree preflight. |
| NTFS component length measured in UTF-16 code units (default: 255) | **Yes**, every profile. | **No explicit UTF-16 check**; its UTF-8 byte cap is normally stricter for valid Unicode. | **Enforced during creation** by the NTFS name-length constraint. |
| UTF-8 component byte limit (default: 255) | **Yes**, portability warning in Windows mode; access-risk check in macOS mode; deliberately off in pure NTFS mode. | **Yes**, byte-aware, configurable sanitization/truncation; best-effort in some edge cases. | **Not a Windows/macOS portability check**; the NTFS name limit uses UTF-16, not UTF-8 bytes. |
| Intended destination prefix included in full-path estimates | **Yes**, optional destination prefix; no destination mount required. | **No**, works on source names rather than forecasting destination paths. | **No preflight**; handles actual paths on the mounted volume. |
| Legacy Windows full-path limits | **Yes**, Windows profile; defaults to 259 UTF-16 units for files and 247 for folders, with a long-path setting. | **No full-path check**. | **No Windows API compatibility check**; a Linux copy can succeed despite later Windows path problems. |
| Extended NTFS/Windows path-range estimate (default: 32,760 UTF-16 units) | **Yes**, every profile; a configurable estimate, not a test of the destination API. | **No full-path check**. | **No future Windows-path estimate**; actual Linux/VFS operation limits still apply. |
| Excessive nesting depth | **Yes**, configurable comfort warning, not a hard filesystem limit. | **No depth warning**; deepest-first directory renaming serves a different purpose. | **No comfort warning**; only actual operation limits. |
| Case-insensitive sibling collisions (`Report.txt` vs `report.txt`) | **Yes**, Windows/macOS profiles, using a conservative comparison. | **No cross-platform sibling scan**; rename-target existence checks on ext4 do not catch case-only pairs. | **No warning by default**; POSIX-namespace names are case-sensitive, even with `windows_names`. |
| Unicode-normalization sibling collisions (visually identical composed/decomposed names) | **Yes**, Windows/macOS profiles, using `NFC + casefold`. | **No normalization-aware sibling scan**. | **No normalization-portability warning**. |
| Individual names not in Unicode NFC form | **Yes**, portability warning in every profile. | **No NFC warning or normalization step**. | **No NFC portability warning**. |
| macOS/Finder colon risk | **Yes**, macOS profile. | **Yes**, colons are already sanitization targets. | **No Finder-specific warning**; `windows_names` can reject the colon for Windows naming reasons. |
| macOS/Finder leading-dot visibility/usability risk | **Yes**, macOS-profile warning; not an NTFS copy blocker. | **No warning**; intentionally preserves existing leading dots. | **No Finder-specific warning**. |
| macOS full-path UTF-8 byte limit (default: 1024) | **Yes**, macOS profile; configurable estimate. | **No full-path check**. | **No forecast of macOS API limits**. |
| Symlink traversal safety and copy-semantics warning | **Yes**, never follows symlinks during traversal and reports them. | **Partial**: recursive traversal avoids/skips symlinks, but does not assess their destination-copy semantics. | **Supports links**, but does not warn whether the chosen copier preserves their intended semantics. |
| Hardlink identity may be lost during copying | **Yes**, warns when a regular file has multiple hardlinks. | **No hardlink-preservation warning**. | **Supports hardlinks**, but cannot ensure that the copier preserves source hardlink identity. |
| FIFOs, sockets, device nodes, and other non-regular objects | **Yes**, flags them as blockers for an ordinary file/folder copy. | **No dedicated ordinary-copy compatibility check**. | **Can represent special files** using Interix/WSL conventions; this does not make them ordinary Windows-compatible files. |
| Unreadable source folders make the assessment incomplete | **Yes**, explicit scan error. | **No comparable compatibility assessment**; traversal/rename errors are not a copy-readiness report. | **Not a source-tree scanner**; source read failures are handled by the copy tool/source filesystem. |
| Separate storage blockers, OS-access risks, and portability warnings | **Yes**, profile-aware issue classes and CSV/JSON reports. | **No equivalent classification**; reports planned/applied renames. | **No preflight report**; returns errors for actual filesystem operations. |

Important qualifications:

- **Default ntfs-3g is permissive, not a Windows-name validator.** Its `windows_names` option closes many basic naming gaps, but does not add Windows full-path forecasting, a normalization audit, or a source-tree collision report. It also does not retroactively validate existing names. The separate `lowntfs-3g` variant has an optional `ignore_case` mode; that is not the default ntfs-3g behavior compared here.
- **Some checks are already implicit in ext4.** Ordinary ext4 components cannot contain NUL or `/` and are limited to 255 bytes. For valid UTF-8 names, that byte limit also keeps the original component within 255 UTF-16 units. These checks remain useful for other source filesystems/programmatic inputs, but are not extra rejection power over an ordinary ext4 tree. CrossRename's byte truncation is not an unconditional guarantee: its implementation permits an overlong extension to remain, and collision suffixes can increase the resulting length.
- **Warnings are not proof of an impossible copy.** NFC differences, `NFC + casefold` collisions, depth thresholds, and macOS/Finder assumptions are conservative portability checks, not exact emulations of every destination's rules. Path checks depend on the supplied destination prefix and configured limits.
- **NTFSafe does not replace the driver or verification.** It does not test destination free space, write permissions, volume health, or successful data transfer. ntfs-3g still handles real destination operations, including capabilities such as links and special-file representations that an ordinary-copy scan intentionally treats cautiously.

The tools have complementary roles: **CrossRename fixes/sanitizes names**, **NTFSafe inspects and explains risks before copying**, and **ntfs-3g performs filesystem operations**. CrossRename offers bulk renaming, optional directory renaming, dry-run mode, and Unicode lookalike replacements; NTFSafe instead provides a GUI/CLI report and optional one-at-a-time manual renaming with F2.

A good workflow is to scan first, manually fix or use a renaming tool for problematic names, **scan again after renaming**, then copy with `rsync` and verify.

## GUI appearance

The GUI is intentionally simple but uses:

- Qt Fusion style with light/dark-aware colors for the full application palette, inputs, tables, headers, and scrollbars; the GUI follows Ubuntu's system appearance setting and updates when the color scheme changes;
- padded controls;
- alternating table rows;
- manually resizable columns;
- a collapsed **Advanced scan options** pane by default to leave more room for rows/columns;
- high-contrast scrollbars with clearer handles; the vertical handle is not fixed-size and should resize with the visible table area;
- tooltip boxes for truncated cells;
- lexicographic sorting by clicking column headers, with visible up/down arrows; multi-problem rows are pinned at the end in case-insensitive A–Z Problem order, independently of column sorting;
- inline F2 renaming that preserves the current scroll position, keeps visible renamed rows selected and focused for keyboard navigation, and marks edited rows with a light-orange background.

Further improvements that would make it feel more like Nautilus would be a custom icon theme, persistent column widths, filter/search controls, a details side panel, and a dark/light theme toggle.

## Development checks

On Ubuntu/Debian, install Qt's native runtime libraries before running GUI tests:

```bash
sudo apt-get update
sudo apt-get install --yes --no-install-recommends libegl1 libgl1
```

Qt still needs these libraries when tests use `QT_QPA_PLATFORM=offscreen`.
In particular, `libegl1` provides `libEGL.so.1`; without it, importing
`PySide6.QtTest` can fail before any GUI test runs. The GitHub Actions workflow
installs these system packages separately from the Python dependencies.

Install the locked project and development dependencies, then run the local checks:

```bash
uv sync --locked --group dev
uv run --locked pre-commit run --all-files
uv run --locked python -m compileall -q src
uv run --locked python src/ntfsafe.py --help
uv run --locked python -m unittest discover -s tests -v
```

GitHub Actions runs these checks on pushes and pull requests. The GUI regression tests run headlessly using Qt's offscreen platform and cover multi-problem ordering, single-problem column sorting, the conditional warning, and selection, focus, keyboard navigation, and scroll preservation after inline renaming. There is not yet a coverage configuration.

For VS Code, run **Python: Configure Tests**, choose **unittest**, select the [`tests`](tests/) directory, and use the `test_*.py` pattern. Keep [`tests/__init__.py`](tests/__init__.py): VS Code's project-based discovery uses the repository as the top-level import directory and requires the test directory to be importable.

## References

- uv script dependencies / PEP 723: <https://docs.astral.sh/uv/guides/scripts/>
- Qt `QDesktopServices.openUrl`: <https://doc.qt.io/qt-6/qdesktopservices.html>
- Microsoft filesystem functionality comparison / NTFS limits: <https://learn.microsoft.com/en-us/windows/win32/fileio/filesystem-functionality-comparison>
- Microsoft Windows filename rules: <https://learn.microsoft.com/en-us/windows/win32/fileio/naming-a-file>
- Microsoft maximum path length: <https://learn.microsoft.com/en-us/windows/win32/fileio/maximum-file-path-limitation>


**Copyright Frieder Simon.**
- Linux NTFS layout / POSIX namespace note: <https://kernel.googlesource.com/pub/scm/linux/kernel/git/torvalds/linux/+/865730eec5435ce40a5dc3c615077d04c8f95098/fs/ntfs/layout.h>
- CrossRename: <https://github.com/Jemeni11/CrossRename>
- CrossRename sanitization, traversal, and rename-collision implementation: <https://github.com/Jemeni11/CrossRename/blob/main/src/crossrename/rename.py>
- ntfs-3g manual, including Windows filename compatibility and `windows_names`: <https://manpages.debian.org/bookworm/ntfs-3g/ntfs-3g.8.en.html>
- ntfs-3g Unicode conversion and creation implementation (2022.10.3): [unistr.c](https://github.com/tuxera/ntfs-3g/blob/2022.10.3/libntfs-3g/unistr.c), [dir.c](https://github.com/tuxera/ntfs-3g/blob/2022.10.3/libntfs-3g/dir.c).
