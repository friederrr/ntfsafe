import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

os.environ["QT_QPA_PLATFORM"] = "offscreen"

from PySide6.QtCore import Qt, QThread  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import (  # noqa: E402
    QApplication,
    QLineEdit,
    QMainWindow,
    QMessageBox,
)

from src import ntfsafe  # noqa: E402


def issue(name: str, problem: str, detail: str = "") -> ntfsafe.Issue:
    return ntfsafe.make_issue(
        severity="WARNING",
        issue_kind=ntfsafe.ISSUE_KIND_WINDOWS_ACCESS,
        object_type="file",
        name=name,
        location="/scan",
        relative_path=name,
        absolute_path=f"/scan/{name}",
        problem=problem,
        detail=detail,
        check_id=problem,
    )


class GuiResultsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        def capture_window() -> int:
            cls.app = QApplication.instance()
            cls.window = next(
                widget
                for widget in cls.app.topLevelWidgets()
                if isinstance(widget, QMainWindow)
            )
            return 0

        with patch.object(QApplication, "exec", side_effect=capture_window):
            ntfsafe.run_gui()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.window.close()
        cls.app.processEvents()

    def setUp(self) -> None:
        self.window.edited_records.clear()
        self.window.show_edited_check.setChecked(True)
        self.window.on_finished([], {})
        self.model = type(self.window.model)()
        self.issues = [
            issue("a-last.txt", "Zulu problem"),
            issue("a-last.txt", "Zulu secondary"),
            issue("z-single.txt", "Single A", "a detail"),
            issue("z-first.txt", "alpha problem"),
            issue("z-first.txt", "Beta secondary"),
            issue("a-single.txt", "Single Z", "z detail"),
            issue("c-middle.txt", "Delta problem"),
            issue("c-middle.txt", "Echo secondary"),
        ]
        self.expected_multis = ["z-first.txt", "c-middle.txt", "a-last.txt"]

    def assert_multi_order(self) -> None:
        multis = [item for item in self.model.items if item.is_multi_problem]
        self.assertEqual(
            [item.primary_issue.name for item in multis], self.expected_multis
        )
        self.assertTrue(
            all(item.is_multi_problem for item in self.model.items[-len(multis):])
        )

    def test_default_order_preserves_singles_and_sorts_multis_by_problem(self) -> None:
        self.model.set_issues(self.issues)
        self.assertEqual(
            [item.primary_issue.name for item in self.model.items[:2]],
            ["z-single.txt", "a-single.txt"],
        )
        self.assert_multi_order()

    def test_header_sorting_keeps_multis_in_ascending_problem_order(self) -> None:
        self.model.set_issues(self.issues)
        for column in range(self.model.columnCount()):
            for order in (Qt.SortOrder.AscendingOrder, Qt.SortOrder.DescendingOrder):
                with self.subTest(column=column, order=order):
                    self.model.sort(column, order)
                    self.assert_multi_order()
                    singles = self.model.items[:2]
                    values = [
                        (
                            self.model.display_value(item, column).casefold(),
                            item.primary_issue.absolute_path.casefold(),
                        )
                        for item in singles
                    ]
                    self.assertEqual(
                        values,
                        sorted(
                            values, reverse=order == Qt.SortOrder.DescendingOrder
                        ),
                    )

    def test_refresh_keeps_multis_sorted_with_existing_header_sort(self) -> None:
        self.model.sort(6, Qt.SortOrder.DescendingOrder)
        self.model.set_issues(list(reversed(self.issues)))
        self.assert_multi_order()

    def test_multi_sort_uses_full_problem_text_then_path_for_ties(self) -> None:
        issues = [
            issue("a.txt", "Common"),
            issue("a.txt", "Zulu"),
            issue("c.txt", "Common"),
            issue("c.txt", "Tango"),
            issue("b.txt", "Common"),
            issue("b.txt", "Tango"),
        ]
        self.model.set_issues(issues)
        self.assertEqual(
            [item.primary_issue.name for item in self.model.items],
            ["b.txt", "c.txt", "a.txt"],
        )

    def test_warning_is_conditional_and_bold_below_legend(self) -> None:
        warning = self.window.multi_problem_warning_label
        self.assertTrue(warning.isHidden())
        self.window.on_finished([issue("single.txt", "Single problem")], {})
        self.assertTrue(warning.isHidden())

        self.window.on_finished(self.issues, {})
        self.assertFalse(warning.isHidden())
        self.assertTrue(warning.font().bold())
        self.assertIn("multiple problems", warning.text())
        layout = self.window.centralWidget().layout()
        self.assertEqual(
            layout.indexOf(warning), layout.indexOf(self.window.legend_label) + 1
        )
        for item in self.window.model.items:
            if item.is_multi_problem:
                for column in range(self.window.model.columnCount()):
                    index = self.window.model.index(
                        self.window.model.items.index(item), column
                    )
                    self.assertTrue(index.data(Qt.ItemDataRole.FontRole).bold())

        self.window.on_finished([], {})
        self.assertTrue(warning.isHidden())

    def test_warning_tracks_edited_row_visibility_and_profile_reset(self) -> None:
        self.window.on_finished(
            [issue("edited.txt", "First"), issue("edited.txt", "Second")],
            {"issue_rows": 2},
        )
        self.window.edited_records["/scan/edited.txt"] = ntfsafe.EditedRecord(
            old_absolute_path="/scan/old.txt",
            new_absolute_path="/scan/edited.txt",
            old_name="old.txt",
            new_name="edited.txt",
            object_type="file",
            location="/scan",
            relative_path="edited.txt",
            edited_at="2026-10-21 00:00:00",
        )
        self.window.show_edited_check.setChecked(False)
        self.assertTrue(self.window.multi_problem_warning_label.isHidden())
        self.window.show_edited_check.setChecked(True)
        self.assertFalse(self.window.multi_problem_warning_label.isHidden())
        self.window.profile_combo.setCurrentIndex(
            self.window.profile_combo.findData(ntfsafe.PROFILE_NTFS)
        )
        self.assertTrue(self.window.multi_problem_warning_label.isHidden())
        self.window.profile_combo.setCurrentIndex(
            self.window.profile_combo.findData(ntfsafe.PROFILE_WINDOWS)
        )

    def test_finished_scan_sorts_multi_issue_files_and_folders(self) -> None:
        with TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "a?.txt.").touch()
            (root / "z:folder.").mkdir()
            (root / "single?.txt").touch()
            issues, summary = ntfsafe.scan_tree(
                folder, ntfsafe.settings_for_profile(ntfsafe.PROFILE_WINDOWS)
            )
            self.window.on_finished(issues, summary)

        items = self.window.model.items
        multis = [item for item in items if item.is_multi_problem]
        self.assertEqual(len(multis), 2)
        self.assertEqual(
            {item.primary_issue.object_type for item in multis}, {"file", "folder"}
        )
        problems = [ntfsafe.problem_text_for_item(item).casefold() for item in multis]
        self.assertEqual(problems, sorted(problems))
        self.assertFalse(items[0].is_multi_problem)
        self.assertFalse(self.window.multi_problem_warning_label.isHidden())

    def test_inline_rename_keeps_selection_focus_and_keyboard_navigation(self) -> None:
        for object_type in ("file", "folder"):
            for new_name in ("n-fixed", "0-fixed"):
                with (
                    self.subTest(object_type=object_type, new_name=new_name),
                    TemporaryDirectory() as folder,
                ):
                    root = Path(folder)
                    for name in ("a?item", "m?item", "z?item"):
                        path = root / name
                        if object_type == "folder":
                            path.mkdir()
                        else:
                            path.touch()
                    self.window.edited_records.clear()
                    issues, summary = ntfsafe.scan_tree(folder, self.window.settings())
                    self.window.on_finished(issues, summary)
                    table = self.window.table
                    table.sortByColumn(3, Qt.SortOrder.AscendingOrder)
                    self.app.processEvents()
                    table.setCurrentIndex(self.window.model.index(1, 3))
                    table.setFocus()

                    for old_name, renamed in (
                        ("m?item", new_name),
                        (new_name, "p-fixed"),
                    ):
                        QTest.keyClick(table, Qt.Key.Key_F2)
                        editor = self.app.focusWidget()
                        self.assertIsInstance(editor, QLineEdit)
                        editor.setText(renamed)
                        QTest.keyClick(editor, Qt.Key.Key_Return)
                        QTest.qWait(20)

                        self.assertFalse((root / old_name).exists())
                        self.assertTrue((root / renamed).exists())
                        current = table.currentIndex()
                        self.assertTrue(current.isValid())
                        self.assertEqual(
                            self.window.model.issue_at(current.row()).absolute_path,
                            str(root / renamed),
                        )
                        self.assertEqual(current.column(), 3)
                        self.assertEqual(
                            [
                                index.row()
                                for index in table.selectionModel().selectedRows()
                            ],
                            [current.row()],
                        )
                        self.assertTrue(table.hasFocus())
                        row = current.row()
                        QTest.keyClick(table, Qt.Key.Key_Down)
                        self.assertEqual(table.currentIndex().row(), row + 1)
                        QTest.keyClick(table, Qt.Key.Key_Up)
                        self.assertEqual(table.currentIndex().row(), row)

    def test_inline_rename_preserves_scroll_for_multi_problem_row(self) -> None:
        with TemporaryDirectory() as folder:
            root = Path(folder)
            for number in range(60):
                (root / f"item-{number:03d}?.txt.").touch()
            self.window.on_finished(
                *ntfsafe.scan_tree(folder, self.window.settings())
            )
            table = self.window.table
            table.sortByColumn(3, Qt.SortOrder.DescendingOrder)
            self.app.processEvents()
            index = self.window.model.index(20, 3)
            self.assertTrue(self.window.model.items[index.row()].is_multi_problem)
            original = Path(self.window.model.issue_at(index.row()).absolute_path)
            table.setCurrentIndex(index)
            table.setFocus()
            table.scrollTo(index, table.ScrollHint.PositionAtCenter)
            table.horizontalScrollBar().setValue(100)
            QTest.keyClick(table, Qt.Key.Key_F2)
            editor = self.app.focusWidget()
            self.assertIsInstance(editor, QLineEdit)
            old_v = table.verticalScrollBar().value()
            old_h = table.horizontalScrollBar().value()
            self.assertGreater(old_v, 0)
            self.assertGreater(old_h, 0)
            editor.setText("zz-fixed.txt")
            QTest.keyClick(editor, Qt.Key.Key_Return)
            QTest.qWait(20)

            self.assertFalse(original.exists())
            self.assertTrue((root / "zz-fixed.txt").exists())
            current = table.currentIndex()
            self.assertEqual(current.row(), self.window.model.rowCount() - 1)
            self.assertEqual(current.data(), "zz-fixed.txt")
            self.assertEqual(
                [index.row() for index in table.selectionModel().selectedRows()],
                [current.row()],
            )
            self.assertTrue(table.hasFocus())
            self.assertEqual(table.verticalScrollBar().value(), old_v)
            self.assertEqual(table.horizontalScrollBar().value(), old_h)
            problems = [
                ntfsafe.problem_text_for_item(item).casefold()
                for item in self.window.model.items
                if item.is_multi_problem
            ]
            self.assertEqual(problems, sorted(problems))
            QTest.keyClick(table, Qt.Key.Key_Up)
            self.assertEqual(table.currentIndex().row(), current.row() - 1)

    def test_inline_rename_still_hides_edited_rows_when_filter_is_off(self) -> None:
        with TemporaryDirectory() as folder:
            root = Path(folder)
            for name in ("a?item", "m?item", "z?item"):
                (root / name).touch()
            self.window.show_edited_check.setChecked(False)
            self.window.on_finished(
                *ntfsafe.scan_tree(folder, self.window.settings())
            )
            table = self.window.table
            table.sortByColumn(3, Qt.SortOrder.AscendingOrder)
            self.app.processEvents()
            table.setCurrentIndex(self.window.model.index(1, 3))
            QTest.mouseClick(self.window.rename_btn, Qt.MouseButton.LeftButton)
            editor = self.app.focusWidget()
            self.assertIsInstance(editor, QLineEdit)
            editor.setText("fixed")
            QTest.keyClick(editor, Qt.Key.Key_Return)
            QTest.qWait(20)

            self.assertTrue((root / "fixed").exists())
            self.assertEqual(self.window.model.rowCount(), 2)
            self.assertFalse(any(item.edited for item in self.window.model.items))
            self.assertEqual(table.selectionModel().selectedRows(), [])
            self.assertTrue(table.hasFocus())

    def test_rescan_rechecks_renamed_files_and_folders(self) -> None:
        for object_type in ("file", "folder"):
            for new_name in ("fixed", "still?invalid"):
                for show_edited in (True, False):
                    with (
                        self.subTest(
                            object_type=object_type,
                            new_name=new_name,
                            show_edited=show_edited,
                        ),
                        TemporaryDirectory() as folder,
                    ):
                        original = Path(folder) / "bad?name"
                        if object_type == "folder":
                            original.mkdir()
                        else:
                            original.touch()
                        self.window.edited_records.clear()
                        self.window.show_edited_check.setChecked(True)
                        settings = self.window.settings()
                        issues, summary = ntfsafe.scan_tree(folder, settings)
                        self.window.on_finished(issues, summary)
                        self.assertTrue(self.window.rename_issue(issues[0], new_name))
                        self.assertTrue(self.window.model.items[0].edited)
                        self.assertIsNotNone(
                            self.window.model.index(0, 3).data(
                                Qt.ItemDataRole.BackgroundRole
                            )
                        )
                        self.window.show_edited_check.setChecked(show_edited)
                        self.window.source_edit.setText(folder)

                        with patch.object(QThread, "start") as start_thread:
                            self.window.start_scan()
                        self.addCleanup(self.window.thread.deleteLater)
                        start_thread.assert_called_once_with()
                        self.assertEqual(self.window.edited_records, {})
                        self.assertEqual(self.window.model.rowCount(), 0)

                        issues, summary = ntfsafe.scan_tree(folder, settings)
                        self.window.on_finished(issues, summary)
                        self.assertEqual(self.window.model.edited_paths, set())
                        if new_name == "fixed":
                            self.assertEqual(self.window.model.rowCount(), 0)
                            self.assertIn(
                                "Problematic items: 0", self.window.count_label.text()
                            )
                        else:
                            self.assertEqual(self.window.model.rowCount(), 1)
                            item = self.window.model.items[0]
                            self.assertEqual(item.primary_issue.name, new_name)
                            self.assertEqual(item.primary_issue.object_type, object_type)
                            self.assertFalse(item.edited)
                            self.assertIsNone(
                                self.window.model.index(0, 3).data(
                                    Qt.ItemDataRole.BackgroundRole
                                )
                            )
                            self.assertIn(
                                "Problematic items: 1", self.window.count_label.text()
                            )

                        self.window.show_edited_check.setChecked(True)
                        self.assertFalse(
                            any(item.edited for item in self.window.model.items)
                        )
                        self.assertEqual(
                            self.window.model.rowCount(), int(new_name != "fixed")
                        )

    def test_scanning_another_folder_clears_edited_placeholders(self) -> None:
        self.window.edited_records["/scan/edited.txt"] = ntfsafe.EditedRecord(
            old_absolute_path="/scan/old.txt",
            new_absolute_path="/scan/edited.txt",
            old_name="old.txt",
            new_name="edited.txt",
            object_type="file",
            location="/scan",
            relative_path="edited.txt",
            edited_at="2026-10-21 00:00:00",
        )
        self.window.refresh_table_preserving_view()
        self.assertEqual(self.window.model.rowCount(), 1)
        with TemporaryDirectory() as folder:
            self.window.source_edit.setText(folder)
            with patch.object(QThread, "start"):
                self.window.start_scan()
            self.addCleanup(self.window.thread.deleteLater)
            self.assertEqual(self.window.edited_records, {})
            self.assertEqual(self.window.model.rowCount(), 0)
            issues, summary = ntfsafe.scan_tree(folder, self.window.settings())
            self.window.on_finished(issues, summary)
            self.assertEqual(self.window.model.rowCount(), 0)

    def test_invalid_scan_preserves_edited_rows(self) -> None:
        self.window.on_finished([issue("bad?.txt", "Invalid character")], {})
        self.window.edited_records["/scan/edited.txt"] = ntfsafe.EditedRecord(
            old_absolute_path="/scan/old.txt",
            new_absolute_path="/scan/edited.txt",
            old_name="old.txt",
            new_name="edited.txt",
            object_type="file",
            location="/scan",
            relative_path="edited.txt",
            edited_at="2026-10-21 00:00:00",
        )
        self.window.refresh_table_preserving_view()
        previous_items = list(self.window.model.items)
        previous_records = dict(self.window.edited_records)
        self.window.source_edit.clear()
        with patch.object(QMessageBox, "warning") as warning:
            self.window.start_scan()
        warning.assert_called_once()
        self.assertEqual(self.window.edited_records, previous_records)
        self.assertEqual(self.window.model.items, previous_items)


if __name__ == "__main__":
    unittest.main()
