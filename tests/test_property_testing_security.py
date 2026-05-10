#!/usr/bin/env python3

import sys
from pathlib import Path

import pytest


AF_ROOT = Path(__file__).resolve().parent.parent
if str(AF_ROOT) not in sys.path:
    sys.path.insert(0, str(AF_ROOT))

from adversarial_testing.property_testing import (  # noqa: E402
    _subprocess_eval,
    _validate_assertion,
)


@pytest.mark.parametrize(
    "assertion",
    [
        "compile('1', '<x>', 'eval')",
        "vars()",
        "dir([])",
        "locals()",
        "globals()",
        "eval('1')",
        "exec('x=1')",
        "__import__('os')",
    ],
)
def test_validate_assertion_rejects_dangerous_builtin_calls(assertion):
    assert _validate_assertion(assertion) is False


@pytest.mark.parametrize(
    "assertion",
    [
        "().__class__.__mro__[-1].__subclasses__()",
        "getattr((), '_' + '_class__')",
        "lambda x: x",
    ],
)
def test_validate_assertion_rejects_introspection_escapes(assertion):
    assert _validate_assertion(assertion) is False


def test_subprocess_eval_rejects_subclasses_traversal():
    with pytest.raises(ValueError, match="unsafe sandbox code rejected"):
        _subprocess_eval("().__class__.__mro__[-1].__subclasses__()", {}, {})


def test_subprocess_eval_allows_simple_property_expression():
    assert _subprocess_eval("x + 1 == 3", {}, {"x": 2}) is True
