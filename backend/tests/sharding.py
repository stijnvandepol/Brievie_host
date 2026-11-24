"""Split the test run over parallel CI jobs, by test file.

Loaded as a pytest plugin (`-p tests.sharding`) only where the run is sharded; a plain
`pytest` ignores all of it. `--shard 2/6` keeps the files of the second of six shards and
deselects the rest. Every file lands in exactly one shard: the files are dealt out longest
first to the lightest shard (weights in `shard_weights.json`, seconds measured in CI; a
file that is not listed weighs 1), so adding a test file needs no change here.
`--shard-report PATH` writes how many tests this shard kept and how many there are, so the
job that waits for all shards can check that together they are the whole suite.
"""

import json
from pathlib import Path

import pytest

WEIGHTS = Path(__file__).with_name("shard_weights.json")


def pytest_addoption(parser: pytest.Parser) -> None:
    group = parser.getgroup("shard")
    group.addoption("--shard", default=None, help="run shard I of N, as I/N (1-based)")
    group.addoption(
        "--shard-report", default=None, help="write the shard's counts to this JSON file"
    )


def parse_shard(value: str) -> tuple[int, int]:
    index_text, _, total_text = value.partition("/")
    index, total = int(index_text), int(total_text)
    if not 1 <= index <= total:
        raise pytest.UsageError(f"--shard {value}: need 1 <= I <= N")
    return index, total


def assign(weights: dict[str, float], total: int) -> dict[str, int]:
    """File name to shard number (1-based): longest first, onto the lightest shard."""
    load = [0.0] * total
    shard_of: dict[str, int] = {}
    for name in sorted(weights, key=lambda n: (-weights[n], n)):
        lightest = min(range(total), key=lambda i: (load[i], i))
        load[lightest] += weights[name]
        shard_of[name] = lightest + 1
    return shard_of


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    value = config.getoption("--shard")
    if value is None:
        return
    index, total = parse_shard(value)
    known: dict[str, float] = json.loads(WEIGHTS.read_text())
    root = Path(config.rootpath)
    names = {item: str(item.path.relative_to(root)) for item in items}
    weights = {name: float(known.get(name, 1)) for name in set(names.values())}
    shard_of = assign(weights, total)
    kept = [item for item in items if shard_of[names[item]] == index]
    dropped = [item for item in items if shard_of[names[item]] != index]
    if dropped:
        config.hook.pytest_deselected(items=dropped)
    items[:] = kept
    report = config.getoption("--shard-report")
    if report:
        Path(report).write_text(
            json.dumps(
                {
                    "shard": index,
                    "shards": total,
                    "selected": len(kept),
                    "total": len(kept) + len(dropped),
                }
            )
        )


# TEMPORARY: per-file timing summary, to be removed before merge.
_FILE_SECONDS: dict[str, float] = {}


def pytest_runtest_logreport(report: pytest.TestReport) -> None:
    name = report.nodeid.split("::")[0]
    _FILE_SECONDS[name] = _FILE_SECONDS.get(name, 0.0) + report.duration


def pytest_terminal_summary(terminalreporter: object) -> None:
    for name, seconds in sorted(_FILE_SECONDS.items()):
        print(f"FILETIME {name} {seconds:.2f}")
