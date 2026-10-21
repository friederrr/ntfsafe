"""Guard against native Qt crashes that occur after test assertions pass.

PySide6 6.12.0's Signal.emit() lost one reference to Python's True object per
emission on Python 3.10. Repeated emissions could abort the interpreter during
shutdown, even after unittest printed OK. Newer CPython versions with immortal
booleans can hide this bug, so Python 3.10 CI coverage remains important.

This tests the Qt binding and process shutdown, not GUI behavior. A child
interpreter emits signals without needing a GUI, event loop, or display.
Requiring its exit status to be zero also checks interpreter cleanup. A native
abort cannot be caught as a Python exception; the subprocess lets us report it
as an ordinary test failure with captured diagnostics instead.
"""

import subprocess
import sys
import textwrap
import unittest


class QtRuntimeTests(unittest.TestCase):
    def test_signal_emission_exits_cleanly(self) -> None:
        script = textwrap.dedent(
            """
            from PySide6.QtCore import QObject, Signal

            class Emitter(QObject):
                changed = Signal()

            emitter = Emitter()
            for _ in range(100):
                emitter.changed.emit()
            """
        )
        result = subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertEqual(
            result.returncode,
            0,
            f"Qt signal subprocess failed:\n{result.stdout}\n{result.stderr}",
        )


if __name__ == "__main__":
    unittest.main()
