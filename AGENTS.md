# Agent guide for NTFSafe

## Scope

These instructions apply throughout this repository. If a subtree adds its own
`AGENTS.md`, follow its more specific instructions there. This file uses the
[AGENTS.md format](https://agents.md/); see [README.md](README.md) for end-user
documentation and detailed compatibility assumptions.

## Using the project safely

- NTFSafe inspects names, paths, and metadata in mounted folders. It is not a
  copier, raw-volume checker, or file-content verifier. Do not mount, repair,
  rename, or copy user data just to perform a scan.
- Prefer the CLI for automation. Supplying a folder selects the CLI; omitting
  it opens the GUI. Run commands from the repository root:

  ```bash
  uv run --script src/ntfsafe.py --help
  uv run --script src/ntfsafe.py /path/to/source --profile windows --json /path/to/report.json
  ```

  Replace the example paths with the intended input and output. Report exports
  write files; put them outside the scanned tree and avoid overwriting user data.
  There is no installed `ntfsafe` console entry point.
- Choose `windows` for ordinary Windows use on NTFS, `macos` for macOS/Finder
  assumptions, or `ntfs` for filesystem-level representability. The source
  filesystem does not select the profile. Use `--dest-root` when the eventual
  destination prefix is known; otherwise path estimates are source-relative.
- CLI exit codes are `0` when there are no error-level findings, `1` when there
  are error-level findings, and `2` for invalid arguments or an invalid source
  folder. Exit code `0` can still mean warnings: inspect the report rather than
  treating it as proof of a safe copy.
- For source-checkout integration, import `ntfsafe` from `src`, construct settings
  with `settings_for_profile()`, and call `scan_tree()`. It returns issues and a
  summary; check `completed` and `cancelled` before treating results as complete.
  These are source-level functions, not a separately packaged stable API.
- Scanning must stay read-only. The existing explicit, single-item GUI rename
  action is the exception; never invoke it automatically as part of scanning.

## Manual GUI renames and rescans

- Select a file/folder and press **F2** or click **Rename selected…** to edit the
  **Name** column. Press **Enter** to rename the actual source item in place.
  This is a manual, one-item operation, not an automatic or bulk sanitizer.
- Update the row text immediately after a successful rename, preserve the table's
  scroll position, and mark the row with a pencil icon and light-orange background.
  **Show edited items** hides/shows items renamed since the last scan. The marker
  is not proof that the compatibility problems were fixed; findings and counts
  are refreshed by scanning again. Edited-item placeholders are display-only,
  excluded from unresolved counts and CSV/JSON findings.
- Keep edited state scan-local. In `MainWindow.start_scan()`, clear
  `edited_records` after validating the source folder and before refreshing the
  table or starting the worker. An invalid source must leave existing results and
  edited rows intact. Do not reset edited state merely when rendering results.
- Every new scan checks all items in the selected folder, including previously
  renamed files/folders. Clear old edited markers and placeholders whether the
  source folder is unchanged or different. Items without findings disappear;
  remaining findings appear as normal problem rows without the edited highlight,
  even when **Show edited items** is unchecked.
- Keep this behavior explained consistently in the GUI **Help / About** section
  and [README.md](README.md), and retain regression coverage for rescanning edited
  files/folders, hidden edited rows, a different folder, and invalid scan input.

## Development environment

- Support Python 3.10 and newer; do not introduce syntax or APIs unavailable on
  3.10. CI currently runs Python 3.10.
- Use `uv` and the committed lockfile:

  ```bash
  uv sync --locked --group dev
  ```

- PySide6 is required for GUI work and GUI tests. Keep Qt imports lazy so the
  command-line scanner and core module remain usable without importing PySide6.
- `uv run --script` reads the script's inline dependency metadata. In a prepared
  development environment, use `uv run --locked python ...`; do not assume that
  `uv run python ...` processes inline script metadata.

## Code map

The canonical application is [src/ntfsafe.py](src/ntfsafe.py), not a package split
across several implementation modules:

- `Settings`, `PROFILE_SCAN_DEFAULTS`, and `settings_for_profile()` define shared
  configuration and profile defaults.
- `analyze_component()` and `analyze_path()` implement checks.
  `scan_tree()` traverses the source, detects sibling collisions, and summarizes
  findings.
- `Issue` and `make_issue()` define findings. `write_csv()`, `write_json()`, and
  `issue_to_report_dict()` handle exports.
- `ProblemItem`, `group_problem_items()`, and the text/sort helpers translate
  individual findings into per-item GUI rows.
- `run_gui()` defines the Qt model, view, and main window locally. `_ScanWorker`
  adapts scanning to the GUI worker thread.
- `parse_args()` and `main()` implement CLI parsing, settings overrides, and exit
  status.

Tests live in [tests/](tests/). GUI regression tests are in
[test_gui_results.py](tests/test_gui_results.py); desktop integration tests are in
[test_file_manager.py](tests/test_file_manager.py). Keep
[tests/__init__.py](tests/__init__.py) so project-root unittest discovery works.

## Extending the project

- Reuse shared check, settings, and reporting helpers rather than implementing
  separate GUI and CLI rules. Keep the single-script launch path working.
- For a new check, use `make_issue()` with a descriptive, stable `check_id` and
  the appropriate severity and issue kind. Distinguish NTFS storage blockers,
  OS-access risks, and portability warnings; do not turn conservative warnings
  into claims that a copy is impossible.
- When adding an option or profile, wire every applicable surface: settings,
  shared defaults, analysis, CLI arguments and overrides, GUI controls and
  profile-specific visibility, in-app help, exports, tests, and documentation.
  Keep both README comparison tables consistent with any changed coverage.
- Preserve measurement units: NTFS and Windows limits use UTF-16 code units;
  UTF-8 portability and macOS limits use bytes. Reuse `utf16_units()`,
  `utf8_bytes()`, and the existing destination-path helpers. Do not substitute
  Python character counts for either unit.
- Keep raw filesystem paths intact for filesystem operations. Use `safe_display()`
  at display/export boundaries; escaped display strings must not replace the
  original paths. Preserve surrogateescape handling and Unicode collision logic.
- Do not follow symlinks during traversal or read file contents to perform
  metadata checks. Keep progress, cancellation, and explicit incomplete-scan
  diagnostics working.
- Preserve the difference between issue counts and unique problematic-item
  counts. Each path contributes once to the latter, under its most serious class.
  Exports retain individual findings; GUI rows group findings by item.
- In GUI work, preserve multi-problem rows pinned at the end in case-insensitive
  ascending Problem-text order, independently of column sorting. Keep rename
  refreshes from losing scroll position, and exclude edited-item placeholders
  from unresolved counts. Keep scanning off the GUI thread and surface failures.
- Follow existing type-annotated Python and Qt conventions. Ruff is configured
  for Python 3.10, an 88-character line length, and `E4`, `E7`, `E9`, `F`, and `I`
  lint rules. Avoid unrelated whole-file formatting or refactoring.
- For dependency changes, keep [pyproject.toml](pyproject.toml) and the script's
  PEP 723 metadata aligned where applicable; regenerate [uv.lock](uv.lock) with
  `uv lock`, rather than editing it by hand. Keep `APP_VERSION` and project
  metadata consistent when intentionally changing the version.

## Validation

- Add or update regression tests for behavior changes. Use temporary directories
  for filesystem fixtures and mock desktop/process launchers; never exercise
  renaming, copying, or application launches against real user data in tests.
- For new length checks, test the exact limit and the first value beyond it,
  including non-BMP Unicode where UTF-16 differs from character count. For
  collision/profile changes, cover relevant case and normalization pairs and
  verify both enabled and deliberately disabled behavior.
- Use unittest, not an assumed pytest setup. Start with the affected module or
  test, for example:

  ```bash
  uv run --locked python -m unittest tests.test_gui_results -v
  ```

  GUI tests set `QT_QPA_PLATFORM=offscreen` before importing Qt and capture the
  window by patching `QApplication.exec`; extend that pattern instead of opening
  interactive windows in automation.
- Before submitting code changes, run the checks defined in
  [.github/workflows/quality.yml](.github/workflows/quality.yml):

  ```bash
  uv run --locked pre-commit run --all-files
  uv run --locked python -m compileall -q src
  uv run --locked python src/ntfsafe.py --help
  uv run --locked python -m unittest discover -s tests -v
  ```

  Pre-commit includes whitespace/EOF fixers that may modify files; inspect their
  changes without discarding pre-existing work. For documentation-only changes,
  check the affected Markdown files; Python tests are unnecessary.
- In the handoff or pull request, summarize behavior changes, validation actually
  run, and any limitations or failures. Update user-facing documentation when
  behavior changes, and this guide when agent workflows or code structure change.
