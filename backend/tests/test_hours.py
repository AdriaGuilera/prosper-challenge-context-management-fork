from datetime import time

import pytest

from store.hours import HoursError, parse_hours, summarize


@pytest.mark.parametrize("text,expected", [
    ("Mon-Fri 8:00-17:00", {d: [(time(8), time(17))] for d in range(5)}),
    ("Sat 9-13", {5: [(time(9), time(13))]}),
    ("Mon,Wed,Fri 7:30am-3pm", {d: [(time(7, 30), time(15))] for d in (0, 2, 4)}),
    ("Tue 8:00-12:00, 13:00-18:00", {1: [(time(8), time(12)), (time(13), time(18))]}),
    ("Daily 9-21; Sun closed", {d: [(time(9), time(21))] for d in range(6)}),
    ("Mon-Fri 9-1pm", {d: [(time(9), time(13))] for d in range(5)}),
    ("Weekends 10am-2pm", {5: [(time(10), time(14))], 6: [(time(10), time(14))]}),
])
def test_parses(text, expected):
    assert parse_hours(text) == expected


@pytest.mark.parametrize("text", ["", "by appointment", "Mon-Fri 17:00-8:00", "Fri-Mon 9-5",
                                  "Mon 8-12, 11-14", "Mon 25:00-26:00", "Sun closed"])
def test_rejects(text):
    with pytest.raises(HoursError):
        parse_hours(text)


def test_times_compare_as_times_not_strings():
    # "9:00" > "17:00" lexicographically; parsed values must not invert
    (opens, closes), = parse_hours("Mon 9:00-17:00")[0]
    assert opens < closes


def test_summary_groups_days():
    assert summarize(parse_hours("Mon-Fri 8:00-17:00")) == "Monday to Friday 8am to 5pm, closed Saturday and Sunday"
