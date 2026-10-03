"""Rules as config data: checking an edited rule list and writing it to ``config.toml``.

Used by the engine for the rules API and by the GUI's rules editor. Nothing here
touches the event loop or Qt. See ``docs/design/rules-editor.md``.
"""

from __future__ import annotations

import hashlib
import math
import os
import re
import shutil
import tomllib
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Literal

import tomlkit
from pydantic import ValidationError

from estim_camming.rules import Rule, wiring_problems

Level = Literal["error", "warning", "info"]
Interval = tuple[int, float]  # inclusive token range; high may be math.inf


class RulesFileError(Exception):
    """The rules could not be written to the config file."""


class RulesConflict(Exception):
    """The rules changed since the editor loaded them (stale revision)."""

    def __init__(self, revision: int) -> None:
        super().__init__(f"the rules were changed elsewhere (now revision {revision})")
        self.revision = revision


class RulesInvalid(Exception):
    """The edited rules don't validate; nothing was changed."""

    def __init__(self, check: RulesCheck) -> None:
        super().__init__(f"{len(check.errors)} error(s) in the rules")
        self.check = check


# -- rules <-> config data ----------------------------------------------------------


#: Whole numbers of seconds are written as ints (``duration = 5``, not ``5.0``).
_SECONDS = ("duration", "duration_max", "duration_per_token")


#: Key order in a written rule; anything else follows in model order.
_KEY_ORDER = (
    "name",
    "label",
    "tokens",
    "min_tokens",
    "max_tokens",
    "pattern",
    "params",
    "intensity",
    "duration",
    "duration_max",
    "duration_per_token",
    "show_in_menu",
    "channels",
)


def rule_to_data(rule: Rule) -> dict[str, Any]:
    """A rule as a compact ``[[rules]]`` table: defaults left out, ``tokens = N``
    for an exact amount."""
    data = rule.model_dump(exclude_defaults=True, exclude_none=True)
    data.pop("tokens", None)
    if rule.min_tokens == rule.max_tokens:
        data.pop("min_tokens", None)
        data.pop("max_tokens", None)
        data["tokens"] = rule.min_tokens
    else:
        data["min_tokens"] = rule.min_tokens
    if isinstance(rule.channels, dict):
        data["channels"] = {
            ch: _ordered(spec.model_dump(exclude_none=True)) for ch, spec in rule.channels.items()
        }
    for key in _SECONDS:
        value = data.get(key)
        if isinstance(value, float) and value.is_integer():
            data[key] = int(value)
    return _ordered(data)


def _ordered(data: dict[str, Any]) -> dict[str, Any]:
    order = {key: i for i, key in enumerate(_KEY_ORDER)}
    return dict(sorted(data.items(), key=lambda kv: order.get(kv[0], len(order))))


def rules_to_toml(rules: Sequence[Rule]) -> str:
    """The ``[[rules]]`` block for ``rules``, one blank line between rules."""
    aot = tomlkit.aot()
    for rule in rules:
        data = rule_to_data(rule)
        table = tomlkit.table()
        for key, value in data.items():
            if key == "channels" and isinstance(value, dict):
                channels = tomlkit.table(is_super_table=True)
                for ch, spec in value.items():
                    sub = tomlkit.table()
                    for k, v in spec.items():
                        sub[k] = _inline(v)
                    channels[ch] = sub
                table["channels"] = channels
            else:
                table[key] = _inline(value)
        aot.append(table)
    doc = tomlkit.document()
    doc["rules"] = aot
    text = re.sub(r"\n{2,}", "\n", tomlkit.dumps(doc).strip())
    # The style of the example config: params = { period = 1.0 }
    text = re.sub(r"^(\w+ = )\{(.+)\}$", r"\1{ \2 }", text, flags=re.MULTILINE)
    # One blank line between rules; channel tables stay attached to their rule.
    return text.replace("\n[[rules]]", "\n\n[[rules]]") + "\n"


def _inline(value: Any) -> Any:
    if isinstance(value, dict):
        table = tomlkit.inline_table()
        for k, v in value.items():
            table[k] = _inline(v)
        return table
    return value


# -- checking -----------------------------------------------------------------------


@dataclass(frozen=True)
class Problem:
    level: Level
    message: str
    rule: int | None = None  # index in the list, None for the list as a whole
    field: str = ""  # dotted path in the rule, e.g. "channels.A.params.period"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class RulesCheck:
    rules: list[Rule] | None  # None if any rule is invalid
    problems: list[Problem]

    @property
    def errors(self) -> list[Problem]:
        return [p for p in self.problems if p.level == "error"]

    @property
    def ok(self) -> bool:
        return self.rules is not None and not self.errors

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "errors": [p.to_dict() for p in self.errors],
            "warnings": [p.to_dict() for p in self.problems if p.level != "error"],
        }


def check_rules(data: Any, device_channels: Sequence[str], max_action_seconds: float) -> RulesCheck:
    """Validate an edited rule list (config-shaped dicts) the way start-up does,
    plus warnings for rules that can never match, capped durations and gaps."""
    if not isinstance(data, list):
        return RulesCheck(None, [Problem("error", "rules must be a list")])
    if not data:
        return RulesCheck(None, [Problem("error", "at least one rule is required")])
    rules: list[Rule] = []
    problems: list[Problem] = []
    for index, item in enumerate(data):
        try:
            rule = Rule.model_validate(item)
        except ValidationError as exc:
            for error in exc.errors():
                field = ".".join(str(part) for part in error["loc"])
                message = error["msg"].removeprefix("Value error, ")
                problems.append(Problem("error", message, index, field))
            continue
        for field, message in wiring_problems(rule, device_channels):
            problems.append(Problem("error", message, index, field))
        rules.append(rule)
    if len(rules) != len(data):
        return RulesCheck(None, problems)

    seen: dict[str, int] = {}
    for index, rule in enumerate(rules):
        if rule.name in seen:
            problems.append(
                Problem("error", f"name already used by rule {seen[rule.name] + 1}", index, "name")
            )
        seen.setdefault(rule.name, index)
    problems += _match_problems(rules)
    problems += _duration_problems(rules, max_action_seconds)
    return RulesCheck(rules, problems)


def _interval(rule: Rule) -> Interval:
    assert rule.min_tokens is not None
    return rule.min_tokens, math.inf if rule.max_tokens is None else rule.max_tokens


def _subtract(intervals: list[Interval], cut: Interval) -> list[Interval]:
    result: list[Interval] = []
    for low, high in intervals:
        if cut[1] < low or cut[0] > high:
            result.append((low, high))
            continue
        if low < cut[0]:
            result.append((low, cut[0] - 1))
        if high > cut[1]:
            result.append((int(cut[1]) + 1, high))
    return result


def format_range(low: int, high: float) -> str:
    if high == math.inf:
        return f"{low}+"
    return f"{low}" if high == low else f"{low}–{int(high)}"


def _ranges(intervals: list[Interval]) -> str:
    shown = ", ".join(format_range(low, high) for low, high in intervals[:4])
    return shown + (", …" if len(intervals) > 4 else "")


def uncovered_amounts(rules: Sequence[Rule]) -> list[Interval]:
    """Token ranges no rule takes (tips of these amounts trigger nothing)."""
    uncovered: list[Interval] = [(1, math.inf)]
    for rule in rules:
        uncovered = _subtract(uncovered, _interval(rule))
    return uncovered


def _match_problems(rules: list[Rule]) -> list[Problem]:
    """First match wins: rules covered (fully or partly) by earlier ones, and gaps."""
    problems = []
    for index, rule in enumerate(rules):
        own = _interval(rule)
        remaining = [own]
        earlier = []
        for number, other in enumerate(rules[:index], start=1):
            after = _subtract(remaining, _interval(other))
            if after != remaining:
                earlier.append(f'{number} "{other.display_label}"')
            remaining = after
        if not earlier:
            continue
        by = ", ".join(earlier[:3]) + (", …" if len(earlier) > 3 else "")
        if not remaining:
            problems.append(
                Problem(
                    "warning",
                    f"never matches: rule {by} is checked first and takes "
                    f"{format_range(*own)}. Move this rule up.",
                    index,
                    "tokens",
                )
            )
        else:
            taken: list[Interval] = [own]
            for part in remaining:
                taken = _subtract(taken, part)
            problems.append(
                Problem("info", f"tips of {_ranges(taken)} go to rule {by} first", index, "tokens")
            )
    uncovered = uncovered_amounts(rules)
    if uncovered:
        problems.append(Problem("info", f"tips of {_ranges(uncovered)} tokens trigger nothing"))
    return problems


def _duration_problems(rules: list[Rule], max_action_seconds: float) -> list[Problem]:
    """Rules whose duration can exceed the cap (S6 cuts the action short)."""
    problems = []
    cap = max_action_seconds
    capped = f"cut to safety.max_action_seconds ({cap:g} s)"
    for index, rule in enumerate(rules):
        low, high = _interval(rule)
        longest = rule.duration_max if rule.duration_max is not None else rule.duration
        per_token = rule.duration_per_token
        if rule.duration + per_token * low > cap:
            problems.append(Problem("warning", f"always {capped}", index, "duration"))
        elif per_token > 0:
            # Smallest tip whose (longest) duration exceeds the cap.
            first = max(low, math.floor((cap - longest) / per_token) + 1)
            if first <= high:
                problems.append(
                    Problem(
                        "warning",
                        f"tips of {first}+ tokens can be {capped}",
                        index,
                        "duration_per_token",
                    )
                )
        elif longest > cap:
            problems.append(
                Problem(
                    "warning",
                    f"random durations above {cap:g} s are {capped}",
                    index,
                    "duration_max",
                )
            )
    return problems


# -- the config file ----------------------------------------------------------------

_HEADER = re.compile(r"^\s*\[\[?\s*([^\]]+?)\s*\]\]?\s*(#.*)?$")


def _header_root(line: str) -> str | None:
    """First key of a table header line, or None if the line isn't a header."""
    match = _HEADER.match(line)
    if not match:
        return None
    return match.group(1).split(".")[0].strip().strip("\"'")


def splice_rules(text: str, rules_block: str) -> str:
    """``text`` with its ``[[rules]]`` tables replaced by ``rules_block``.

    Everything outside the rules, including comments, is kept. Comment lines
    directly above the next section stay with that section. Comments between
    rules are lost (the block is regenerated). Raises RulesFileError if the
    rules aren't one contiguous block of ``[[rules]]``/``[rules.*]`` tables.
    """
    lines = text.splitlines(keepends=True)
    roots = [_header_root(line) for line in lines]
    starts = [i for i, root in enumerate(roots) if root == "rules"]
    if not starts:
        # No [[rules]] tables (e.g. an inline array): let tomlkit replace the key.
        doc = tomlkit.parse(text)
        doc.pop("rules", None)
        body = tomlkit.dumps(doc).rstrip("\n")
        return (body + "\n\n" if body else "") + rules_block
    first = starts[0]
    end = len(lines)
    for i in range(first + 1, len(lines)):
        if roots[i] is not None and roots[i] != "rules":
            end = i
            break
    if any(root == "rules" for root in roots[end:]):
        raise RulesFileError(
            "the [[rules]] tables in the config file are split up by other sections; "
            "move them together to save rules from the editor"
        )
    # Comments and blank lines right above the next section belong to it.
    keep_from = end
    while keep_from > first + 1 and (
        not lines[keep_from - 1].strip() or lines[keep_from - 1].lstrip().startswith("#")
    ):
        keep_from -= 1
    tail = lines[keep_from:end] + lines[end:]
    if end < len(lines) and (not tail or tail[0].strip()):
        tail = ["\n", *tail]
    return "".join(lines[:first]) + rules_block + "".join(tail)


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class RulesFile:
    """Writes rules into the config file the engine was started with.

    Refuses to overwrite the file if it changed on disk since the engine loaded
    (or last wrote) it, so hand edits are never lost. The first write in a
    session keeps a copy as ``<name>.bak``. Writes are atomic (temp file +
    ``os.replace``). Blocking: call it from a worker thread.
    """

    def __init__(self, path: str | Path, loaded_sha256: str) -> None:
        self.path = Path(path)
        self._sha = loaded_sha256
        self._backed_up = False

    def changed_on_disk(self) -> bool:
        try:
            return sha256(self.path.read_bytes()) != self._sha
        except OSError:
            return True

    def writable(self) -> bool:
        return os.access(self.path, os.W_OK) and os.access(self.path.parent, os.W_OK)

    def info(self) -> dict[str, Any]:
        return {
            "path": str(self.path),
            "writable": self.writable(),
            "changed_on_disk": self.changed_on_disk(),
        }

    def write(self, rules: Sequence[Rule]) -> None:
        try:
            raw = self.path.read_bytes()
        except OSError as exc:
            raise RulesFileError(f"cannot read {self.path}: {exc}") from exc
        if sha256(raw) != self._sha:
            raise RulesFileError(
                f"{self.path.name} was changed by hand since the engine loaded it; restart "
                "the engine to load it (the new rules are live but not saved)"
            )
        old_text = raw.decode()
        new_text = splice_rules(old_text, rules_to_toml(rules))
        _verify(old_text, new_text, rules)
        tmp = self.path.with_name(self.path.name + ".tmp")
        try:
            if not self._backed_up:
                shutil.copy2(self.path, self.path.with_name(self.path.name + ".bak"))
                self._backed_up = True
            with tmp.open("w", encoding="utf-8", newline="") as fh:
                fh.write(new_text)
                fh.flush()
                os.fsync(fh.fileno())
            shutil.copymode(self.path, tmp)
            os.replace(tmp, self.path)
        except OSError as exc:
            tmp.unlink(missing_ok=True)
            raise RulesFileError(f"cannot write {self.path}: {exc}") from exc
        self._sha = sha256(new_text.encode())


def _verify(old_text: str, new_text: str, rules: Sequence[Rule]) -> None:
    """The new file must read back as the old one with exactly the new rules."""
    try:
        old, new = tomllib.loads(old_text), tomllib.loads(new_text)
    except tomllib.TOMLDecodeError as exc:
        raise RulesFileError(f"internal error: the new config would not parse: {exc}") from exc
    old.pop("rules", None)
    written = new.pop("rules", None)
    if old != new:
        raise RulesFileError("internal error: saving rules would change other settings")
    if [rule_to_data(Rule.model_validate(r)) for r in written or []] != [
        rule_to_data(r) for r in rules
    ]:
        raise RulesFileError("internal error: the rules would not read back the same")
