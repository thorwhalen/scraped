"""The acquisition subagent's contract: bounded effort in, a report (never a payload) out.

Two mechanical pieces the acquisition subagent (and anything else driving the
ladder in a loop) builds on:

- `AcquisitionBudget` — numeric stop rules bounded by steps, failures **and** wall
  clock. Every attempt goes through one context manager, so every failure path
  (a raised challenge, a robots refusal, a timeout, a bug) increments the *same*
  counter. A ceiling with holes in it — some failures counted, others not — is the
  known bug class this closes.
- `AcquisitionReport` — what comes back: a required `stopped_reason`, claims each
  marked `Validated? YES/PARTIAL/NO`, artifact *paths* (the bytes are in the store),
  a self-critique, and a hard word budget. Returning a summary rather than page text
  is also a security control: scraped content is hostile input, and it never
  crosses into the caller's context.

>>> budget = AcquisitionBudget(StopRules(max_steps=2))
>>> with budget.attempt("probe"):
...     pass
>>> with budget.attempt("fetch"):
...     raise ValueError("boom")
>>> budget.steps, budget.failures, budget.stopped_reason
(2, 1, 'max_steps')
"""

from __future__ import annotations

import time
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Callable, Iterator, Sequence

__all__ = [
    "StopRules",
    "AcquisitionBudget",
    "BudgetExhausted",
    "Validated",
    "Claim",
    "SelfCritique",
    "AcquisitionReport",
    "report_json_schema",
    "DEFAULT_WORD_BUDGET",
]

#: Default hard word budget for a report: long enough for a table of claims and a
#: critique, short enough that nobody is tempted to paste page text into it.
DEFAULT_WORD_BUDGET = 400

# Stop reasons a budget can produce on its own; a caller may also stop for its own
# reasons ("done", "challenge", "robots", ...), which are recorded verbatim.
STOP_MAX_STEPS = "max_steps"
STOP_MAX_FAILURES = "max_failures"
STOP_MAX_SECONDS = "max_seconds"


class BudgetExhausted(RuntimeError):
    """Raised by `AcquisitionBudget.attempt` when a stop rule has already fired."""


@dataclass(frozen=True)
class StopRules:
    """Numeric ceilings on one acquisition job. All three bind; the first hit wins."""

    max_steps: int = 12
    max_failures: int = 3
    max_seconds: float = 300.0


@dataclass
class AcquisitionBudget:
    """Counts steps, failures and elapsed time against `StopRules`.

    Use `attempt` for every unit of work. It counts the step before running it and
    counts a failure for *any* exception, whatever its type, so there is exactly
    one place a failure can be recorded. Exceptions are swallowed by default (the
    loop goes on to its next move) and kept in `errors`; pass `reraise=True` to
    propagate after counting.

    >>> clock = iter([0.0, 0.0, 400.0]).__next__
    >>> b = AcquisitionBudget(StopRules(max_seconds=300), clock=clock)
    >>> with b.attempt("probe"):
    ...     pass
    >>> b.stopped_reason
    'max_seconds'
    """

    rules: StopRules = field(default_factory=StopRules)
    clock: Callable[[], float] = time.monotonic
    steps: int = 0
    failures: int = 0
    errors: list[tuple[str, str]] = field(default_factory=list)
    _stopped: str | None = None
    _started: float | None = None

    def __post_init__(self):
        self._started = self.clock()

    @property
    def elapsed(self) -> float:
        return self.clock() - self._started

    @property
    def stopped_reason(self) -> str | None:
        """Why the job stopped, or None while it may continue."""
        if self._stopped:
            return self._stopped
        if self.failures >= self.rules.max_failures:
            return STOP_MAX_FAILURES
        if self.steps >= self.rules.max_steps:
            return STOP_MAX_STEPS
        if self.elapsed >= self.rules.max_seconds:
            return STOP_MAX_SECONDS
        return None

    def stop(self, reason: str) -> None:
        """Stop for a caller-given reason (e.g. ``"done"``, ``"challenge"``)."""
        self._stopped = self._stopped or reason

    def record_failure(self, label: str, error: BaseException | str) -> None:
        """The single place a failure is counted."""
        self.failures += 1
        self.errors.append((label, repr(error) if isinstance(error, BaseException) else error))

    @contextmanager
    def attempt(self, label: str, *, reraise: bool = False) -> Iterator[None]:
        """Run one step under the budget. Raises `BudgetExhausted` if already stopped."""
        reason = self.stopped_reason
        if reason:
            raise BudgetExhausted(f"{label!r} refused: stopped ({reason})")
        self.steps += 1
        try:
            yield
        except Exception as error:  # every failure path, by design
            self.record_failure(label, error)
            if reraise:
                raise


class Validated(str, Enum):
    """Whether a claim was checked against the captured bytes."""

    YES = "YES"
    PARTIAL = "PARTIAL"
    NO = "NO"


@dataclass(frozen=True)
class Claim:
    """One finding, with how far it was validated and where its evidence is on disk."""

    text: str
    validated: Validated = Validated.NO
    evidence: tuple[str, ...] = ()

    def __post_init__(self):
        object.__setattr__(self, "validated", Validated(self.validated))
        object.__setattr__(self, "evidence", tuple(self.evidence))


@dataclass(frozen=True)
class SelfCritique:
    """What the report does not cover, and why it might be wrong."""

    gaps: tuple[str, ...] = ()
    skipped_steps: tuple[str, ...] = ()
    assumptions: tuple[str, ...] = ()
    staleness_risk: str = ""

    def __post_init__(self):
        for name in ("gaps", "skipped_steps", "assumptions"):
            object.__setattr__(self, name, tuple(getattr(self, name)))


def _words(text: str) -> int:
    return len(text.split())


@dataclass(frozen=True)
class AcquisitionReport:
    """What the acquisition subagent returns. Never a payload: paths to artifacts.

    >>> r = AcquisitionReport(
    ...     stopped_reason="done",
    ...     claims=[Claim("listing data is in __NEXT_DATA__", "YES", ["runs/a.json"])],
    ...     artifacts=["runs/a.json"],
    ... )
    >>> r.check() is r
    True
    >>> print(r.to_markdown().splitlines()[0])
    **Stopped:** done
    """

    stopped_reason: str
    claims: Sequence[Claim] = ()
    artifacts: Sequence[str] = ()
    critique: SelfCritique = field(default_factory=SelfCritique)
    word_budget: int = DEFAULT_WORD_BUDGET

    def __post_init__(self):
        object.__setattr__(
            self,
            "claims",
            tuple(c if isinstance(c, Claim) else Claim(**c) for c in self.claims),
        )
        object.__setattr__(self, "artifacts", tuple(self.artifacts))
        if isinstance(self.critique, dict):
            object.__setattr__(self, "critique", SelfCritique(**self.critique))

    @classmethod
    def from_budget(cls, budget: AcquisitionBudget, **kwargs) -> "AcquisitionReport":
        """Build a report whose `stopped_reason` comes from the budget that ran the job."""
        reason = budget.stopped_reason or "unfinished"
        return cls(stopped_reason=reason, **kwargs)

    def to_markdown(self) -> str:
        lines = [f"**Stopped:** {self.stopped_reason}", ""]
        if self.claims:
            lines += ["| Claim | Validated? | Evidence |", "|---|---|---|"]
            for c in self.claims:
                lines.append(f"| {c.text} | {c.validated.value} | {', '.join(c.evidence)} |")
            lines.append("")
        if self.artifacts:
            lines += ["**Artifacts:**", *[f"- {p}" for p in self.artifacts], ""]
        crit = self.critique
        lines.append("**Self-critique:**")
        for label, items in (
            ("Gaps", crit.gaps),
            ("Skipped steps", crit.skipped_steps),
            ("Assumptions", crit.assumptions),
        ):
            lines.append(f"- {label}: {'; '.join(items) or 'none stated'}")
        lines.append(f"- Staleness risk: {crit.staleness_risk or 'not assessed'}")
        return "\n".join(lines)

    @property
    def word_count(self) -> int:
        return _words(self.to_markdown())

    def check(self) -> "AcquisitionReport":
        """Raise ValueError if the report breaks its contract; return it otherwise."""
        if not self.stopped_reason or not self.stopped_reason.strip():
            raise ValueError("stopped_reason is required")
        if self.word_count > self.word_budget:
            raise ValueError(
                f"report is {self.word_count} words, over its budget of {self.word_budget}:"
                " move detail into an artifact and return its path"
            )
        return self

    def to_dict(self) -> dict:
        d = asdict(self)
        d["claims"] = [{**asdict(c), "validated": c.validated.value} for c in self.claims]
        return d


def report_json_schema() -> dict:
    """JSON schema of `AcquisitionReport.to_dict`, for an agent's return contract."""
    strings = {"type": "array", "items": {"type": "string"}}
    return {
        "type": "object",
        "properties": {
            "stopped_reason": {"type": "string", "minLength": 1},
            "claims": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "text": {"type": "string"},
                        "validated": {"enum": [v.value for v in Validated]},
                        "evidence": strings,
                    },
                    "required": ["text", "validated"],
                },
            },
            "artifacts": strings,
            "critique": {
                "type": "object",
                "properties": {
                    "gaps": strings,
                    "skipped_steps": strings,
                    "assumptions": strings,
                    "staleness_risk": {"type": "string"},
                },
            },
            "word_budget": {"type": "integer"},
        },
        "required": ["stopped_reason", "claims", "artifacts", "critique"],
    }
