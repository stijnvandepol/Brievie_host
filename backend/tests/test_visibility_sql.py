"""The visibility SQL as text: one condition, rendered for the policy and for `viewers()`.

No database: these tests read the generated statements. What the SQL does is measured
by tests/perf (CI job `visibility-perf`) and, from Task 7 on, tested on the real schema.
"""

import re

import pytest

from apps.workspaces import visibility_sql
from apps.workspaces.visibility_sql import (
    ACTOR,
    DROP_VISIBILITY_FUNCTIONS,
    POLICY_CONDITION,
    document_condition,
    viewer_pairs_sql,
    visibility_functions_sql,
)

MARKERS = {
    "user": "<<USER>>",
    "sees_all": "<<SEES_ALL>>",
    "my_ids": "<<MY_IDS>>",
    "sorts": "<<SORTS>>",
    "covered": "<<COVERED>>",
    "table": "<<TABLE>>",
}

POLICY_ARGS = {
    "user": ACTOR,
    "sees_all": f"(SELECT public.brievie_sees_all_post({ACTOR}))",
    "my_ids": f"(SELECT public.brievie_my_post_ids({ACTOR}))",
    "sorts": f"(SELECT public.brievie_actor_sorts_post({ACTOR}))",
    "covered": "(SELECT public.brievie_covered_post_ids())",
    "table": '"documents_document"',
}

VIEWER_ARGS = {
    "user": "m.user_id",
    "sees_all": "m.sees_all",
    "my_ids": "m.my_ids",
    "sorts": "m.sorts",
    "covered": "c.covered",
    "table": "d",
}


def _substitute(template: str, values: dict[str, str]) -> str:
    for key, marker in MARKERS.items():
        template = template.replace(marker, values[key])
    return template


def test_policy_and_viewer_rendering_share_one_condition() -> None:
    template = document_condition(**MARKERS)
    for marker in MARKERS.values():
        assert marker in template, marker
    policy = _substitute(template, POLICY_ARGS)
    assert f"(SELECT public.brievie_is_system()) OR ({policy})" == POLICY_CONDITION
    assert _substitute(template, VIEWER_ARGS) in viewer_pairs_sql("d.status = %s")


def test_functions_pin_search_path_and_take_the_user() -> None:
    statements = visibility_functions_sql()
    names = [re.search(r"CREATE FUNCTION (public\.\w+)\(", s) for s in statements]
    assert [n.group(1) for n in names if n] == [
        "public.brievie_sees_all_post",
        "public.brievie_my_post_ids",
        "public.brievie_covered_post_ids",
        "public.brievie_actor_sorts_post",
    ]
    for statement in statements:
        assert "SET search_path = ''" in statement
        assert "LANGUAGE sql STABLE PARALLEL SAFE SECURITY INVOKER" in statement
        # The user is a parameter: nothing reads the actor setting inside the functions.
        assert "brievie_actor_id" not in statement
        assert "brievie.actor" not in statement
        if "brievie_covered_post_ids" in statement:
            assert "(uid uuid)" not in statement
            assert "brievie_covered_post_ids()" in statement
        else:
            assert "(uid uuid)" in statement


def test_my_post_ids_takes_only_persons_from_the_person_arm() -> None:
    (my_ids,) = [s for s in visibility_functions_sql() if "brievie_my_post_ids" in s]
    person_arm = my_ids.split("UNION ALL", 1)[0]
    assert "p.user_id = uid" in person_arm
    assert "p.kind = 'person'" in person_arm


def test_every_function_has_a_drop() -> None:
    assert DROP_VISIBILITY_FUNCTIONS == [
        "DROP FUNCTION public.brievie_sees_all_post(uuid)",
        "DROP FUNCTION public.brievie_my_post_ids(uuid)",
        "DROP FUNCTION public.brievie_covered_post_ids()",
        "DROP FUNCTION public.brievie_actor_sorts_post(uuid)",
    ]


def test_condition_has_no_system_arm() -> None:
    assert "brievie_is_system" not in document_condition(**MARKERS)
    assert "brievie_is_system" not in viewer_pairs_sql("true")


def test_condition_qualifies_every_document_column() -> None:
    condition = document_condition(**MARKERS)
    for column in ("visibility", "addressee_ids", "scanned_by_id", "status"):
        for found in re.finditer(rf"([\w<>\"]*\.)?\b{column}\b", condition):
            assert found.group(1) == "<<TABLE>>.", (column, found.group(0))


def test_viewer_pairs_are_scoped_to_the_current_workspace() -> None:
    sql = viewer_pairs_sql("d.status = %s")
    assert "FROM public.workspaces_membership" in sql
    assert "current_setting('brievie.workspace_id', true)" in sql
    assert "(d.status = %s)" in sql


@pytest.mark.parametrize("fragment", ["true; DROP TABLE x", "a -- b", "a /* b */"])
def test_viewer_filter_is_a_single_fragment(fragment: str) -> None:
    with pytest.raises(ValueError, match="fragment"):
        viewer_pairs_sql(fragment)


def test_module_imports_no_django_model() -> None:
    source = visibility_sql.__file__
    assert source
    with open(source) as handle:
        text = handle.read()
    assert "from django" not in text
    assert "import django" not in text
