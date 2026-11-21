"""The CI test shards: every file in exactly one shard, and the plain run untouched."""

import json

import pytest

from tests.sharding import WEIGHTS, assign, parse_shard


def test_every_file_is_in_exactly_one_shard() -> None:
    weights = {f"tests/test_{n}.py": float(n % 7 + 1) for n in range(50)}
    shard_of = assign(weights, 6)
    assert set(shard_of) == set(weights)
    assert set(shard_of.values()) <= set(range(1, 7))


def test_the_shards_are_close_in_weight() -> None:
    weights = {f"tests/test_{n}.py": float(n % 7 + 1) for n in range(50)}
    shard_of = assign(weights, 6)
    loads = [sum(w for name, w in weights.items() if shard_of[name] == i) for i in range(1, 7)]
    assert max(loads) - min(loads) <= max(weights.values())


def test_the_split_does_not_depend_on_the_order() -> None:
    weights = {"a.py": 3.0, "b.py": 3.0, "c.py": 1.0}
    assert assign(weights, 2) == assign(dict(reversed(weights.items())), 2)


@pytest.mark.parametrize("value", ["0/6", "7/6", "x/6"])
def test_a_bad_shard_is_refused(value: str) -> None:
    with pytest.raises((pytest.UsageError, ValueError)):
        parse_shard(value)


def test_the_weights_file_is_a_map_of_seconds() -> None:
    weights = json.loads(WEIGHTS.read_text())
    assert weights
    assert all(name.startswith("tests/") and value > 0 for name, value in weights.items())
