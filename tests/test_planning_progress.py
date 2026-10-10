"""The document checker must accept real progress without claiming app validation."""

import pytest


def plan_text(first_checks):
    return "\n".join(
        f"## Task {number}: example\n" + (first_checks if number == 1 else "- [ ] Pending\n")
        for number in range(1, 10)
    )


@pytest.mark.parametrize("first_checks,expected", [
    ("- [ ] First\n- [ ] Second\n", (0, 9)),
    ("- [x] First\n- [ ] Second\n", (0, 9)),
    ("- [x] First\n- [x] Second\n", (1, 9)),
])
def test_document_progress_counts_only_fully_checked_tasks(first_checks, expected):
    from docs.superpowers.reviews.verify_planning import implementation_progress
    assert implementation_progress(plan_text(first_checks)) == expected


def test_document_progress_rejects_missing_task():
    from docs.superpowers.reviews.verify_planning import implementation_progress
    with pytest.raises(AssertionError):
        implementation_progress("## Task 1: only task\n- [ ] Pending\n")
