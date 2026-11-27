"""Trusted standalone verifier for SINDy Lorenz coefficients; stdlib only.

References: Brunton et al. (2016), Figure 1 / Lorenz equations; PySINDy
v1.7.5 examples/3_original_paper.ipynb, measured-derivative section.
Threshold is an acceptance choice fixed by the maintainer, not a paper claim.
"""

import base64
import itertools
import json
import math
import sys

MAX_BYTES = 32 * 1024
THRESHOLD = 1e-3
POWERS = [()] + [
    term for degree in range(1, 6)
    for term in itertools.combinations_with_replacement(range(3), degree)
]
EXPECTED = [[0.0] * len(POWERS) for _ in range(3)]
for row, term, value in (
    (0, (0,), -10), (0, (1,), 10),
    (1, (0,), 28), (1, (1,), -1), (1, (0, 2), -1),
    (2, (2,), -8 / 3), (2, (0, 1), 1),
):
    EXPECTED[row][POWERS.index(term)] = value


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate key")
        result[key] = value
    return result


def reject_constant(_value):
    raise ValueError("nonfinite constant")


def check_depth(text):
    depth = 0
    quoted = False
    escaped = False
    for char in text:
        if quoted:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                quoted = False
        elif char == '"':
            quoted = True
        elif char in "[{":
            depth += 1
            if depth > 3:
                raise ValueError("excessive nesting")
        elif char in "]}":
            depth -= 1


def validate(encoded):
    if len(encoded) > 4 * ((MAX_BYTES + 2) // 3):
        raise ValueError("oversized input")
    raw = base64.b64decode(encoded, validate=True)
    if len(raw) > MAX_BYTES:
        raise ValueError("oversized input")
    text = raw.decode("utf-8")
    check_depth(text)
    data = json.loads(text, object_pairs_hook=unique_object, parse_constant=reject_constant)
    if type(data) is not dict or set(data) != {"schema_version", "coefficients"}:
        raise ValueError("unexpected schema")
    if type(data["schema_version"]) is not int or data["schema_version"] != 1:
        raise ValueError("unexpected version")
    rows = data["coefficients"]
    if type(rows) is not list or len(rows) != 3:
        raise ValueError("unexpected rows")
    for row in rows:
        if type(row) is not list or len(row) != 56:
            raise ValueError("unexpected columns")
        for value in row:
            if type(value) not in (int, float) or not -1e6 <= value <= 1e6:
                raise ValueError("invalid coefficient")
            if not math.isfinite(value):
                raise ValueError("nonfinite coefficient")
    errors = [
        max(abs(value - ref) for value, ref in zip(row, expected))
        for row, expected in zip(rows, EXPECTED)
    ]
    passed = all(error < THRESHOLD for error in errors)
    return {
        "status": "passed" if passed else "failed",
        "failure_category": None if passed else "metric_not_met",
        "schema_valid": True,
        "errors": errors,
    }


def main():
    try:
        if len(sys.argv) != 2:
            raise ValueError("unexpected arguments")
        result = validate(sys.argv[1])
    except (ValueError, TypeError, RecursionError, OverflowError):
        result = {
            "status": "failed", "failure_category": "artifact_invalid",
            "schema_valid": False, "errors": [],
        }
    print(json.dumps(result, allow_nan=False, separators=(",", ":")))


if __name__ == "__main__":
    main()
