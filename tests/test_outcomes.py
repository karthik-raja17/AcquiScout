from __future__ import annotations

from acquiscout.eval.discovery import InputOutcome, summarize_outcomes


def test_summarize_outcomes_uses_fixed_denominator() -> None:
    outcomes = (
        [InputOutcome.OFFICIAL] * 18
        + [InputOutcome.NON_TARGET] * 2
        + [InputOutcome.UNRESOLVED] * 12
    )

    summary = summarize_outcomes(outcomes)

    assert summary["input_web"] == 32
    assert summary["official"] == 18
    assert summary["non_target"] == 2
    assert summary["unresolved"] == 12
    assert summary["usable_rate"] == 0.5625
    assert summary["non_target_rate"] == 0.0625
    assert summary["unresolved_rate"] == 0.375
