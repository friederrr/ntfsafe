import unittest
from unittest.mock import patch

from src import ntfsafe


class RevealInFileManagerTests(unittest.TestCase):
    def test_platform_helpers_for_files(self) -> None:
        path = "/scan/file.txt"
        cases = (
            ("Windows", ["explorer", "/select,", ntfsafe.os.path.normpath(path)]),
            ("Darwin", ["/usr/bin/open", "-R", path]),
        )
        for system, command in cases:
            with (
                self.subTest(system=system),
                patch.object(ntfsafe.platform, "system", return_value=system),
                patch.object(ntfsafe.os.path, "isdir", return_value=False),
                patch.object(ntfsafe.subprocess, "Popen") as popen,
                patch.object(ntfsafe, "open_with_default_app") as open_default,
            ):
                self.assertTrue(ntfsafe.reveal_in_file_manager(path))
                popen.assert_called_once_with(command)
                open_default.assert_not_called()

    def test_macos_reveal_ignores_path(self) -> None:
        path = "/scan/file with spaces.txt"
        for search_path in ("", "/untrusted/bin"):
            with (
                self.subTest(search_path=search_path),
                patch.dict(ntfsafe.os.environ, {"PATH": search_path}),
                patch.object(ntfsafe.platform, "system", return_value="Darwin"),
                patch.object(ntfsafe.subprocess, "Popen") as popen,
                patch.object(ntfsafe, "open_with_default_app") as open_default,
            ):
                self.assertTrue(ntfsafe.reveal_in_file_manager(path))
                popen.assert_called_once_with(["/usr/bin/open", "-R", path])
                open_default.assert_not_called()

    def test_default_handler_result_is_preserved(self) -> None:
        path = "/scan/item"
        cases = (
            ("Windows", True, path),
            ("Linux", True, path),
            ("Linux", False, "/scan"),
            ("FreeBSD", False, "/scan"),
        )
        for system, is_dir, expected_path in cases:
            for result in (True, False):
                with (
                    self.subTest(system=system, is_dir=is_dir, result=result),
                    patch.object(ntfsafe.platform, "system", return_value=system),
                    patch.object(ntfsafe.os.path, "isdir", return_value=is_dir),
                    patch.object(ntfsafe.subprocess, "Popen") as popen,
                    patch.object(
                        ntfsafe, "open_with_default_app", return_value=result
                    ) as open_default,
                    self.assertNoLogs(ntfsafe.__name__, level="WARNING"),
                ):
                    self.assertIs(ntfsafe.reveal_in_file_manager(path), result)
                    open_default.assert_called_once_with(expected_path)
                    popen.assert_not_called()

    def test_file_without_parent_does_not_open(self) -> None:
        for path in ("file.txt", ""):
            with (
                self.subTest(path=path),
                patch.object(ntfsafe.platform, "system", return_value="Linux"),
                patch.object(ntfsafe.os.path, "isdir", return_value=False),
                patch.object(ntfsafe.subprocess, "Popen") as popen,
                patch.object(ntfsafe, "open_with_default_app") as open_default,
            ):
                self.assertFalse(ntfsafe.reveal_in_file_manager(path))
                popen.assert_not_called()
                open_default.assert_not_called()

    def test_exceptions_are_logged_and_return_false(self) -> None:
        path = "/scan/item"
        cases = (
            ("Windows", False),
            ("Windows", True),
            ("Darwin", False),
            ("Linux", False),
        )
        for system, is_dir in cases:
            for error_type in (OSError, RuntimeError):
                error = error_type("desktop integration unavailable")
                with (
                    self.subTest(
                        system=system, is_dir=is_dir, error=error_type.__name__
                    ),
                    patch.object(ntfsafe.platform, "system", return_value=system),
                    patch.object(ntfsafe.os.path, "isdir", return_value=is_dir),
                    patch.object(ntfsafe.subprocess, "Popen", side_effect=error),
                    patch.object(
                        ntfsafe, "open_with_default_app", side_effect=error
                    ),
                    self.assertLogs(ntfsafe.__name__, level="WARNING") as logs,
                ):
                    self.assertFalse(ntfsafe.reveal_in_file_manager(path))
                    self.assertEqual(len(logs.records), 1)
                    record = logs.records[0]
                    self.assertEqual(
                        record.getMessage(),
                        f"Could not reveal {path!r} in the file manager",
                    )
                    self.assertEqual(record.exc_info[:2], (error_type, error))
                    self.assertIsNotNone(record.exc_info[2])


if __name__ == "__main__":
    unittest.main()
