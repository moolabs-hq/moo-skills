"""Console UI seam. Commands take a UI so tests inject scripted answers and the
interactive prompts never run in CI.
"""
from __future__ import annotations


class ConsoleUI:
    def say(self, msg: str = "") -> None:
        print(msg)

    def confirm(self, msg: str) -> bool:
        return input(f"{msg} [y/N]: ").strip().lower() in ("y", "yes")

    def ask(self, msg: str, default: str | None = None) -> str:
        suffix = f" [{default}]" if default else ""
        ans = input(f"{msg}{suffix}: ").strip()
        return ans or (default or "")

    def choose(self, msg: str, options: list[str]) -> int:
        self.say(msg)
        for i, opt in enumerate(options, 1):
            self.say(f"  {i}. {opt}")
        while True:
            raw = input("> ").strip()
            if raw.isdigit() and 1 <= int(raw) <= len(options):
                return int(raw) - 1
            self.say("Enter a number from the list.")


class ScriptedUI:
    """Test UI: dequeues pre-seeded confirms / answers / choices."""

    def __init__(self, *, confirms=None, answers=None, choices=None):
        self.confirms = list(confirms or [])
        self.answers = list(answers or [])
        self.choices = list(choices or [])
        self.output: list[str] = []

    def say(self, msg: str = "") -> None:
        self.output.append(msg)

    def confirm(self, msg: str) -> bool:
        self.output.append(msg)
        return self.confirms.pop(0)

    def ask(self, msg: str, default: str | None = None) -> str:
        self.output.append(msg)
        if self.answers:
            ans = self.answers.pop(0)
            return ans if ans not in (None, "") else (default or "")
        return default or ""

    def choose(self, msg: str, options: list[str]) -> int:
        self.output.append(msg)
        return self.choices.pop(0)

    def text(self) -> str:
        return "\n".join(self.output)
