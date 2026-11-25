"""`pytest --tervic` wraps each test in a run and fails tests whose run verdict is fail."""

from __future__ import annotations

import pytest

pytest_plugins = ["pytester"]

TESTS = '''
import tervic

@tervic.step(kind="retriever")
def retrieve(state):
    return {**state, "documents": state["docs"]}

def test_good_agent():
    retrieve({"q": "x", "docs": ["d1"]})

def test_silently_broken_agent():
    retrieve({"q": "x", "docs": []})

def test_plain_failure():
    assert 1 == 2

def test_not_an_agent():
    assert True
'''


def test_plugin_fails_silent_failures(pytester: pytest.Pytester) -> None:
    pytester.makepyfile(test_agent=TESTS)
    result = pytester.runpytest("-p", "tervic.pytest_plugin", "--tervic", "-q", "-p", "no:cacheprovider")
    result.assert_outcomes(passed=2, failed=2)
    result.stdout.fnmatch_lines(["*Tervic verdict FAIL*retrieve*TV301*", "*tervic*", "*FAIL*test_silently_broken_agent*"])


def test_plugin_off_by_default(pytester: pytest.Pytester) -> None:
    pytester.makepyfile(test_agent=TESTS)
    result = pytester.runpytest("-p", "tervic.pytest_plugin", "-q", "-p", "no:cacheprovider")
    result.assert_outcomes(passed=3, failed=1)
