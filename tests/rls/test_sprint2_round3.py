"""Review round 3: approval date freezes, confirmation needs complete facts, a part insert/move withdraws all."""
from tests.rls.test_sprint2 import PART, attempt, fresh  # noqa: F401 - `fresh` is a fixture


def test_approval_date_freezes_with_the_other_edition_facts(admin, world, fresh):  # noqa: F811
    admin.execute("update project set approval_date = '2026-10-06' where id = %s", (fresh["project"],))
    admin.execute("insert into rule_result (firm_id, revision_id, rule_id, edition, result, inputs, citation)"
                  " values (%s, %s, 'NCC2025-J6D3-econ-cycle', 'NCC2025', 'PASS', '{}', '{}')",
                  (fresh["firm"], fresh["revision"]))
    designer = world.firms["A"].users["designer"]
    assert not attempt(designer, "update project set approval_date = '2019-01-01' where id = %s", (fresh["project"],))[0]


def test_project_facts_cannot_be_confirmed_while_incomplete(admin, world, fresh):  # noqa: F811
    designer = world.firms["A"].users["designer"]
    call = "select gate1_confirm('project', array[%s]::uuid[])"
    assert not attempt(designer, call, (fresh["project"],))[0]          # approval_date is still null
    admin.execute("update project set approval_date = '2026-10-06' where id = %s", (fresh["project"],))
    assert attempt(designer, call, (fresh["project"],))[0]


def test_inserting_or_moving_a_part_withdraws_every_part_confirmation(admin, world, fresh):  # noqa: F811
    designer = world.firms["A"].users["designer"]
    for pos, cls in ((0, "5"), (1, "2")):
        admin.execute(PART, (fresh["firm"], fresh["project"], pos, cls))
    confirm = "update building_part set confirmed_by = %s, confirmed_at = now() where project_id = %s"
    count = "select count(*) from building_part where project_id = %s and confirmed_by is not null"
    admin.execute(confirm, (designer, fresh["project"]))
    admin.execute(PART, (fresh["firm"], fresh["project"], 5, "6"))
    assert admin.execute(count, (fresh["project"],)).fetchone() == (0,)
    admin.execute(confirm, (designer, fresh["project"]))
    admin.execute("update building_part set position = 9 where project_id = %s and position = 0", (fresh["project"],))
    assert admin.execute(count, (fresh["project"],)).fetchone() == (0,)
