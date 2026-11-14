import io
import re
import unittest
from unittest import mock

from ai_skill_jev import menu
from ai_skill_jev.cli import Deps, _choose

OPTIONS = ["TypeSafe", "Vercel", "OpenRouter", "OpenCode Zen", "Custom"]


def keys(*seq):
    it = iter(seq)
    return lambda: next(it)


def screen(output, width):
    """Tiny terminal: wraps at `width`, understands ESC[nF (up n rows), ESC[2K, cursor show/hide, SGR."""
    rows, row, col = [""], 0, 0
    for token in re.findall(r"\x1b\[[0-9?;]*[A-Za-z]|\n|[^\x1b\n]", output):
        if token == "\n":
            row, col = row + 1, 0
        elif token.startswith("\x1b"):
            if token.endswith("F"):
                row, col = max(row - int(token[2:-1] or 1), 0), 0
            elif token == "\x1b[2K":
                rows[row] = ""
            continue
        else:
            if col == width:
                row, col = row + 1, 0
            while len(rows) <= row:
                rows.append("")
            rows[row] = rows[row][:col].ljust(col) + token + rows[row][col + 1:]
            col += 1
        while len(rows) <= row:
            rows.append("")
    return [r.rstrip() for r in rows if r.strip()]


class StepTest(unittest.TestCase):
    def test_moves_and_wraps(self):
        self.assertEqual(menu.step(0, menu.DOWN, 5), (1, False))
        self.assertEqual(menu.step(0, menu.UP, 5), (4, False))
        self.assertEqual(menu.step(4, menu.DOWN, 5), (0, False))

    def test_digit_jumps_and_enter_selects(self):
        self.assertEqual(menu.step(0, "3", 5), (2, False))
        self.assertEqual(menu.step(0, "9", 5), (0, False))
        self.assertEqual(menu.step(2, menu.ENTER, 5), (2, True))


class RunTest(unittest.TestCase):
    def test_arrows_then_enter(self):
        out = io.StringIO()
        picked = menu._run("Provider", OPTIONS, 0, keys(menu.DOWN, menu.DOWN, menu.UP, menu.ENTER), out,
                           columns=lambda: 80)
        self.assertEqual(picked, 2)
        self.assertIn("Provider: Vercel", out.getvalue())
        self.assertTrue(out.getvalue().endswith(menu._SHOW))

    def test_cancel_raises_and_restores_cursor(self):
        out = io.StringIO()
        with self.assertRaises(KeyboardInterrupt):
            menu._run("Provider", OPTIONS, 0, keys(menu.DOWN, menu.CANCEL), out, columns=lambda: 80)
        self.assertTrue(out.getvalue().endswith(menu._SHOW))

    def test_narrow_terminal_never_wraps_and_redraws_in_place(self):
        long = ["TypeSafe (native API) (recommended)", "Vercel AI Gateway", "OpenRouter", "Custom endpoint"]
        out = io.StringIO()
        picked = menu._run("Provider", long, 0, keys(menu.DOWN, menu.DOWN, menu.UP, menu.ENTER), out,
                           columns=lambda: 40)
        self.assertEqual(picked, 2)
        self.assertEqual(screen(out.getvalue(), 40), ["Provider: Vercel AI Gateway"])

    def test_frame_rows_fit_width(self):
        for width in (12, 40, 80):
            for line in menu.render("Provider", OPTIONS + ["TypeSafe (native API) (recommended)"], 0, width):
                self.assertLess(len(re.sub(r"\x1b\[[0-9;]*m", "", line)), width)


class ChooseTest(unittest.TestCase):
    def deps(self, select, answers=()):
        it = iter(answers)
        return Deps(out=io.StringIO(), ask=lambda prompt: next(it), select=select)

    def test_uses_picker(self):
        self.assertEqual(_choose(self.deps(lambda p, o, d: 4), "Provider", OPTIONS), 4)

    def test_falls_back_to_numbers(self):
        deps = self.deps(lambda p, o, d: None, answers=["7", "3"])
        self.assertEqual(_choose(deps, "Provider", OPTIONS), 3)
        self.assertIn("Enter a number from 1 to 5.", deps.out.getvalue())

    def test_no_tty_returns_none(self):
        with mock.patch.object(menu.sys, "stdin") as stdin, mock.patch.object(menu.sys, "stdout") as stdout:
            stdin.isatty.return_value = False
            stdout.isatty.return_value = False
            self.assertIsNone(menu.arrow_select("Provider", OPTIONS))


if __name__ == "__main__":
    unittest.main()
