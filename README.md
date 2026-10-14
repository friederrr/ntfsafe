# NTFSafe

NTFSafe is a small Python/PySide6 preflight scanner for checking whether a local folder tree can be copied to an NTFS drive without later discovering that some files/folders are unusable on the intended operating system.

It is read-first and conservative. It does **not** copy files, does **not** bulk-rename files, and does **not** modify file contents. The GUI has one manual convenience feature: you can select a problematic item and press **F2** or **Rename selected…** to rename that one source file/folder in place.

**Copyright Frieder Simon.**

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
└── src/
    └── ntfsafe.py
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
- rows marked with the orange **🟧** multi-problem marker have more than one problem; they are pinned at the end and are not reordered when you sort a column; inside those rows, intrinsic name/content problems are listed before path-depth, nesting, and filename/path-length problems;
- double-click the **Location** cell to open the parent folder in the file browser;
- select a row and press **F2** to rename the problematic file/folder in place; after you press Enter, the table preserves its scroll position instead of jumping to the top;
- use **Show edited items** to hide/show rows for files/folders renamed during this session; edited rows are marked with ✏️ and a light-orange background and are not counted as unresolved problems;
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

`Problematic items` counts unique files/folders with at least one finding. The emoji numbers also count unique files/folders, not issue rows. If one item has multiple findings, it is counted once under its most serious class, so `A + B + C = N`. The table shows **one row per problematic file/folder**; the **Problem** column lists all problems found for that item. Rows marked with the orange **🟧** marker have more than one problem and are bold across the entire row. Those multi-problem rows are always kept at the end of the table and are not reordered when you sort a column, so sorting applies only to single-problem rows. Within a multi-problem row, intrinsic name/content problems are listed before structural depth/path/component-length problems. For example, if a file has both an illegal Windows character and a too-long filename/path, the illegal-character problem appears first.

| Icon | Class | Meaning |
|---|---|---|
| ⛔ | NTFS copy blocker | The item is not safely representable as an ordinary NTFS file/folder, or it violates a hard component/object constraint. |
| 🪟 | Windows access risk | The item may exist on NTFS, especially if created via Linux or special APIs, but ordinary Windows tools may fail to open, rename, delete, or display it correctly. |
| 🍎 | macOS access risk | The item may exist on NTFS, but may be too long or collision-prone for ordinary macOS/Finder-style use. |
| 💻 | OS access risk disabled | In pure NTFS mode, OS/application checks are intentionally disabled, so this count should normally remain zero. |
| ⚠️ | Portability warning | The item can usually be copied, but something about it is semantically fragile or may cause future portability/sync problems. |
| 🟧 | Multiple problems marker | This file/folder has more than one independent problem. Multi-problem rows are pinned at the end and are not sorted with the single-problem rows. |
| ✏️ | Edited item marker | This file/folder was renamed from inside the app during this session. Edited rows have a light-orange background and can be hidden or shown with **Show edited items**. They are not counted as unresolved compatibility problems. |

The explanations are shown directly below the summary in the GUI.

### Ordering of multiple problems in one row

When one file or folder has more than one finding, the **Problem** column lists all findings in a deliberate order:

1. intrinsic name/content/metadata problems first, such as illegal characters, reserved names, non-NFC Unicode, symlinks, hardlinks, special files, or collisions;
2. structural length/depth problems afterwards, such as relative depth, full path length, macOS path byte length, or NTFS/component-name length.

This means a row like `bad?name-with-a-very-long-path.txt` will show the illegal-character problem before the length/path problem, because the illegal character is usually the more concrete rename/fix action.

### Manual renames and edited rows

You can rename one selected source file/folder directly from the table by selecting the row and pressing **F2**, or by clicking **Rename selected…**. The rename is performed on the actual filesystem item, using the operating system's normal rename operation.

After you press Enter, the table refreshes without intentionally scrolling back to the top: the vertical and horizontal scrollbars are restored to their previous positions. The renamed row is marked with a **✏️** icon and a light-orange background.

The **Show edited items** checkbox controls whether these session-local edited rows are visible. This is deliberately independent of whether your edit actually solved the compatibility issue. If you rescan and the item no longer has any compatibility finding, the edited row can still be shown as an **Edited item** row while **Show edited items** is enabled. If you hide edited items, they disappear from the table; toggling the checkbox back on shows them again. Edited rows are not included in the problematic-item count and are not exported as unresolved findings in CSV/JSON.

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

After renaming, run **Scan** again to refresh the issue list. The row text is updated immediately and the row is marked with **✏️** plus a light-orange background, but the scanner does not automatically prove that the new name fixed every issue until you rescan. Use **Show edited items** to hide/show items renamed during the current session.

This is meant for manual fixing. It is not a bulk sanitizer.

## Open/show behavior

For portable opening, the GUI uses Qt's `QDesktopServices.openUrl(QUrl.fromLocalFile(...))`. This asks the operating system to open a local file URL with the associated application or file manager.

For **Show location**, the tool uses best-effort platform-specific helpers:

| OS | Method |
|---|---|
| Windows | `explorer /select,` |
| macOS | `open -R` |
| Linux | DBus `org.freedesktop.FileManager1.ShowItems` when available, otherwise opens the parent folder |

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

## Comparison with CrossRename

CrossRename is a command-line renaming/sanitization tool intended to harmonize file and directory names across Linux, Windows, and macOS. Its README describes features such as Windows-compatible sanitization, recursive operation, dry-run mode, optional directory renaming, optional Unicode lookalike replacements, symlink-loop avoidance, and byte-aware filename truncation.

This tool deliberately has a different purpose: it is a read-first NTFS/Windows/macOS preflight scanner. It tells you what is risky before you copy, and it only renames a file/folder when you explicitly edit one selected row.

| Area | CrossRename | NTFSafe |
|---|---|---|
| Main purpose | Rename/sanitize names so they become cross-platform compatible. | Preflight scan before copying to an NTFS drive. |
| Safety model | Can modify names when run without dry-run. | Scans without changing anything; only changes a name when you explicitly rename one selected item. |
| Interface | CLI. | PySide6 GUI plus CLI. |
| Reports | Logs planned/applied renames. | Scrollable/sortable GUI table with one row per problematic item plus CSV/JSON exports. |
| Manual fixing | Can perform renames automatically. | Lets you locate items and optionally rename a selected item manually with F2; edited rows are marked with ✏️, can be hidden/shown, and do not force the table to scroll to the top. |
| Windows-forbidden characters such as `?`, `*`, `<`, `>` | Yes, as rename/sanitization targets. | Yes, but only in the Windows compatibility profile; pure NTFS deliberately does not flag them. |
| Colon `:` | Treated as a cross-platform/Windows-incompatible rename target. | Split by profile: Windows colon/ADS risk in Windows mode; macOS/Finder colon risk in macOS mode; not a pure-NTFS blocker. |
| Windows reserved names | Yes. | Yes, in the Windows compatibility profile. |
| Trailing spaces/periods | Yes. | Yes, in the Windows compatibility profile. |
| Control characters | Yes. | Yes, in the Windows compatibility profile; NUL is also a pure-NTFS blocker for completeness. |
| Byte-aware 255-byte filename check | Yes. | Yes; warning in Windows mode, macOS access risk in macOS mode, disabled in pure NTFS mode because NTFS names are UTF-16-based. |
| Symlink recursion safety | Yes, avoids symlink loops. | Yes, never follows symlinks during traversal and reports symlinks as portability warnings. |
| Directory renaming | Optional. | Manual one-at-a-time rename only. |
| Unicode alternatives/lookalikes | Optional rename strategy. | Not supported; scanner only. |
| Selectable pure NTFS vs Windows vs macOS interpretation | No; it is a cross-platform sanitizer. | Yes. |
| NTFS-specific storage checks | Not its main focus. | Yes: Unicode validity, NUL/slash namespace constraint, 255 UTF-16 component length, 32,760-character path estimate, special-file detection. |
| Intended destination prefix path estimate | Not its main focus. | Yes; blank destination prefix is supported for Linux-only preflight. |
| Legacy `MAX_PATH` risk | Not its main focus. | Yes, in the Windows compatibility profile. |
| macOS path/component byte checks | Not its main focus. | Yes, in the macOS compatibility profile. |
| macOS Finder colon / leading-dot warnings | Not its main focus. | Yes, in the macOS compatibility profile. |
| NFC normalization warnings | Listed as an edge-case/contribution area in CrossRename context. | Yes, checked. |
| Case/Unicode collision scan | Not the main documented feature. | Yes, checked per directory in Windows/macOS profiles. |
| Hardlink/special-file warnings | Not the main documented feature. | Yes. |

Relationship:

```text
CrossRename = fix/sanitize names.
NTFSafe = inspect/report before copying, with selectable NTFS/Windows/macOS interpretation.
```

A good workflow is to run this scanner first, manually fix or use a renaming tool for problematic names, then copy with `rsync` and verify.

## GUI appearance

The GUI is intentionally simple but uses:

- Qt Fusion style with light/dark-aware colors for the full application palette, inputs, tables, headers, and scrollbars; the GUI follows Ubuntu's system appearance setting and updates when the color scheme changes;
- padded controls;
- alternating table rows;
- manually resizable columns;
- a collapsed **Advanced scan options** pane by default to leave more room for rows/columns;
- high-contrast scrollbars with clearer handles; the vertical handle is not fixed-size and should resize with the visible table area;
- tooltip boxes for truncated cells;
- lexicographic sorting by clicking column headers, with visible up/down arrows; multi-problem rows are pinned at the end and not sorted with the single-problem rows;
- inline F2 renaming that preserves the current scroll position and marks edited rows with a light-orange background.

Further improvements that would make it feel more like Nautilus would be a custom icon theme, persistent column widths, filter/search controls, a details side panel, and a dark/light theme toggle.

## Development checks

Install the locked project and development dependencies, then run the local checks:

```bash
uv sync --locked --group dev
uv run --locked pre-commit run --all-files
uv run --locked python -m compileall -q src
uv run --locked python src/ntfsafe.py --help
```

GitHub Actions runs these checks on pushes and pull requests. There is not yet an automated behavioral test suite or coverage configuration.

## References

- uv script dependencies / PEP 723: <https://docs.astral.sh/uv/guides/scripts/>
- Qt `QDesktopServices.openUrl`: <https://doc.qt.io/qt-6/qdesktopservices.html>
- Microsoft filesystem functionality comparison / NTFS limits: <https://learn.microsoft.com/en-us/windows/win32/fileio/filesystem-functionality-comparison>
- Microsoft Windows filename rules: <https://learn.microsoft.com/en-us/windows/win32/fileio/naming-a-file>
- Microsoft maximum path length: <https://learn.microsoft.com/en-us/windows/win32/fileio/maximum-file-path-limitation>
- Linux NTFS layout / POSIX namespace note: <https://kernel.googlesource.com/pub/scm/linux/kernel/git/torvalds/linux/+/865730eec5435ce40a5dc3c615077d04c8f95098/fs/ntfs/layout.h>
- CrossRename: <https://github.com/Jemeni11/CrossRename>
