"""The Caddyfile enforces the body limit and answers proxy errors in the API shape."""

import json
import re
from pathlib import Path

CADDYFILE = (Path(__file__).resolve().parents[2] / "deploy" / "Caddyfile").read_text()


def error_bodies() -> dict[str, dict[str, str]]:
    bodies = {}
    for body in re.findall(r"respond `(\{.*?\})`", CADDYFILE):
        parsed = json.loads(body)
        bodies[parsed["code"]] = parsed
    return bodies


def test_body_limit_comes_from_the_shared_variable() -> None:
    assert re.search(r"request_body\s*\{\s*max_size \{\$BRIEVIE_MAX_BODY_BYTES\}", CADDYFILE)


def test_proxy_errors_use_the_error_shape() -> None:
    bodies = error_bodies()
    assert set(bodies) == {"payload_too_large", "server_unavailable"}
    for body in bodies.values():
        assert set(body) == {"code", "message"}
    assert bodies["payload_too_large"]["message"] == "Dit verzoek is te groot."
    assert re.search(r"handle_errors 413\b", CADDYFILE)
    assert re.search(r"handle_errors 502 503 504\b", CADDYFILE)
