"""Stop rules and the report contract of the acquisition subagent (scraped#6). Offline."""

import pytest

from scraped.acquire import (
    AcquisitionBudget,
    AcquisitionReport,
    BudgetExhausted,
    ChallengeEncountered,
    Claim,
    RobotsDisallowed,
    SelfCritique,
    StopRules,
    Validated,
    report_json_schema,
)


def _ticking(times):
    it = iter(times)
    last = [0.0]

    def clock():
        last[0] = next(it, last[0])
        return last[0]

    return clock


@pytest.mark.parametrize(
    "error",
    [
        RuntimeError("challenge"),
        PermissionError("robots"),
        TimeoutError("slow"),
        KeyError("bug"),
    ],
)
def test_every_failure_type_increments_the_same_counter(error):
    budget = AcquisitionBudget(StopRules(max_failures=1))
    with budget.attempt("step"):
        raise error
    assert budget.failures == 1
    assert budget.stopped_reason == "max_failures"
    with pytest.raises(BudgetExhausted):
        with budget.attempt("next"):
            pass
    assert budget.steps == 1  # a refused attempt is not a step


def test_package_exceptions_are_counted_too():
    budget = AcquisitionBudget()
    for exc_type in (ChallengeEncountered, RobotsDisallowed):
        with budget.attempt(exc_type.__name__):
            raise exc_type.__new__(exc_type)
    assert budget.failures == 2
    assert [label for label, _ in budget.errors] == ["ChallengeEncountered", "RobotsDisallowed"]


def test_reraise_counts_before_propagating():
    budget = AcquisitionBudget()
    with pytest.raises(ValueError):
        with budget.attempt("x", reraise=True):
            raise ValueError
    assert budget.failures == 1


def test_steps_and_wall_clock_bound():
    budget = AcquisitionBudget(StopRules(max_steps=2, max_seconds=1e9))
    for _ in range(2):
        with budget.attempt("s"):
            pass
    assert budget.stopped_reason == "max_steps"

    clocked = AcquisitionBudget(StopRules(max_seconds=10), clock=_ticking([0, 5, 11]))
    with clocked.attempt("a"):
        pass
    assert clocked.stopped_reason == "max_seconds"


def test_caller_stop_reason_wins_and_sticks():
    budget = AcquisitionBudget()
    budget.stop("done")
    budget.stop("other")
    assert budget.stopped_reason == "done"
    assert AcquisitionReport.from_budget(budget).stopped_reason == "done"


def test_report_requires_stopped_reason_and_respects_word_budget():
    with pytest.raises(ValueError, match="stopped_reason"):
        AcquisitionReport(stopped_reason=" ").check()
    long = AcquisitionReport(stopped_reason="done", claims=[Claim("word " * 50)], word_budget=30)
    with pytest.raises(ValueError, match="over its budget"):
        long.check()


def test_report_markdown_and_dict_roundtrip():
    report = AcquisitionReport(
        stopped_reason="max_failures",
        claims=[{"text": "api at /graphql", "validated": "PARTIAL", "evidence": ["a.har"]}],
        artifacts=["a.har"],
        critique={"gaps": ["page 2 not fetched"], "staleness_risk": "prices change daily"},
    )
    md = report.to_markdown()
    assert "| api at /graphql | PARTIAL | a.har |" in md
    assert "Skipped steps: none stated" in md
    assert report.claims[0].validated is Validated.PARTIAL
    assert isinstance(report.critique, SelfCritique)
    d = report.to_dict()
    assert AcquisitionReport(**d) == report
    schema = report_json_schema()
    assert set(schema["required"]) <= set(d)
    assert "stopped_reason" in schema["required"]
