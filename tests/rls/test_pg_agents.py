"""Runtime agents end to end on the real database: the tool layer, the notes and the call log, the doors they use, and the SDK adapter's setup.
Needs the local Supabase."""
import json
from types import SimpleNamespace

import psycopg
import pytest
from fastapi.testclient import TestClient
from mep.agents.runtime import BUILTIN_TOOLS, Reply, ScriptedRuntime, SdkRuntime
from mep.agents.tools import Agent, Context, ToolLayer
from mep.api import agents as agents_api
from mep.api.agents_pg import PgAgentBackend
from mep.api.schedule import CurrentUser
from mep.api.server import create_pg_app
from mep.engine.loader import load_pack

from tests.rls import test_pg_api as h
from tests.rls import test_pg_lineage as lin
from tests.rls.conftest import DB_URL, as_user

SPEC_FIELDS = {"mark": "AGENT-L1", "storey": {"name": "Level 1", "floor_to_floor_mm": 3600},
               "rooms": [{"name": "Plant", "kind": "plant_room", "height_mm": 3000,
                          "outline": {"type": "rectangle", "x_mm": 0, "y_mm": 0, "width_mm": 6000, "depth_mm": 4000}}]}


@pytest.fixture(scope="module")
def pack():
    return load_pack(lin.ROOT / "rules")


@pytest.fixture(scope="module")
def client(pack):
    return TestClient(create_pg_app(DB_URL, h.SECRET, pack, supabase_url=lin.SUPABASE_URL, anon_key=lin.ANON))


def use_runtime(client, script):
    client.app.dependency_overrides[agents_api.get_runtime] = lambda: ScriptedRuntime(script)


def ask(client, f, agent, role="designer", message="please help"):
    return client.post(f"/revisions/{f['revision']}/agents/{agent}/message", headers=h.auth(f[role]), json={"message": message})


def layer_for(f, agent, role="designer", pack=None):
    user = CurrentUser(user_id=f[role], firm_id=f["firm"], role=role)
    return ToolLayer(Context(agent, role, f["revision"]), PgAgentBackend(DB_URL, user, f["revision"], pack))


# ---- the API and the log ---------------------------------------------------------------------------------------------

def test_asking_an_agent_needs_model_access_and_the_right_person(admin, client, monkeypatch):
    f = h.seed(admin)
    client.app.dependency_overrides.pop(agents_api.get_runtime, None)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    r = ask(client, f, "designer")
    assert r.status_code == 503 and r.json()["detail"]["code"] == "agents_unavailable" and "ANTHROPIC_API_KEY" in r.json()["detail"]["message"]
    assert client.get("/agents/status", headers=h.auth(f["designer"])).json()["configured"] is False
    use_runtime(client, [])
    assert ask(client, f, "designer", role="checker").status_code == 403                  # a checker cannot start the designer's assistant
    assert ask(client, f, "adversarial_checker", role="checker").status_code == 200
    assert ask(client, f, "no_such_agent").status_code == 422
    other = h.seed(admin)
    assert client.post(f"/revisions/{f['revision']}/agents/designer/message", headers=h.auth(other["designer"]), json={"message": "hi"}).status_code == 404
    assert client.post(f"/revisions/{f['revision']}/agents/designer/message", json={"message": "hi"}).status_code == 401


def test_every_forbidden_action_is_refused_logged_and_changes_nothing(admin, client):
    f = h.populate and h.seed(admin)
    script = [("raise_flag", {"severity": "low", "text": "the designer agent may not flag"}), ("approve_rule", {"rule": "x"}),
              ("sign_gate", {"gate": "gate3"}), ("run_skill", {"skill": "../../etc", "use_draft": True}), ("read_results", {})]
    use_runtime(client, script)
    r = ask(client, f, "designer")
    assert r.status_code == 200
    calls = r.json()["calls"]
    assert [(c["tool"], c["denied"]) for c in calls[:3]] == [("raise_flag", True), ("approve_rule", True), ("sign_gate", True)]
    assert calls[3]["ok"] is False and calls[4]["ok"] is True
    rows = admin.execute("select tool, allowed from agent_call where revision_id = %s order by id", (f["revision"],)).fetchall()
    assert rows[:3] == [("raise_flag", False), ("approve_rule", False), ("sign_gate", False)]
    assert admin.execute("select count(*) from agent_note where revision_id = %s", (f["revision"],)).fetchone()[0] == 0
    assert admin.execute("select count(*) from rule_result where revision_id = %s", (f["revision"],)).fetchone()[0] == 0
    assert admin.execute("select count(*) from ledger_event where revision_id = %s and kind = 'agent_call_recorded'", (f["revision"],)).fetchone()[0] == len(rows)


def test_flags_and_risks_are_notes_people_read_and_close(admin, client):
    f = h.seed(admin)
    use_runtime(client, [("raise_flag", {"severity": "medium", "text": "The Gate 1 inputs were confirmed in one click."})])
    assert ask(client, f, "adversarial_checker", role="checker").status_code == 200
    use_runtime(client, [("raise_risk", {"category": "scope", "severity": "low", "text": "No plant room drawn yet."})])
    assert ask(client, f, "compliance_risk", role="checker").status_code == 200
    notes = client.get(f"/revisions/{f['revision']}/agent-notes", headers=h.auth(f["checker"])).json()
    assert sorted((n["agent"], n["kind"], n["status"]) for n in notes) == [("adversarial_checker", "flag", "open"), ("compliance_risk", "risk", "open")]
    assert client.get(f"/revisions/{f['revision']}/agent-notes?kind=flag", headers=h.auth(f["checker"])).json()[0]["kind"] == "flag"
    nid = notes[0]["id"]
    assert client.post(f"/revisions/{f['revision']}/agent-notes/{nid}/resolve", headers=h.auth(f["checker"]), json={"status": "dismissed"}).status_code == 200
    assert client.post(f"/revisions/{f['revision']}/agent-notes/{nid}/resolve", headers=h.auth(f["checker"]), json={"status": "dismissed"}).status_code == 404
    assert client.post(f"/revisions/{f['revision']}/agent-notes/{notes[1]['id']}/resolve", headers=h.auth(f["checker"]), json={"status": "deleted"}).status_code == 422
    other = h.seed(admin)
    assert client.get(f"/revisions/{f['revision']}/agent-notes", headers=h.auth(other["designer"])).status_code == 404
    assert client.post(f"/revisions/{f['revision']}/agent-notes/{notes[1]['id']}/resolve", headers=h.auth(other["checker"]), json={"status": "answered"}).status_code == 404


def test_the_database_itself_keeps_agents_to_their_own_kinds_and_notes_append_only(admin):
    f = h.seed(admin)

    def insert(agent, kind, body="a note of some length"):
        admin.execute("insert into agent_note (firm_id, revision_id, agent, kind, body, created_by) values (%s, %s, %s, %s, %s, %s)",
                      (f["firm"], f["revision"], agent, kind, body, f["designer"]))

    for agent, kind in (("adversarial_checker", "explanation"), ("adversarial_checker", "risk"), ("compliance_risk", "flag"),
                        ("designer", "flag"), ("designer", "risk"), ("designer", "fix_hypothesis")):
        with pytest.raises(psycopg.errors.CheckViolation):
            insert(agent, kind)
    insert("designer", "fix_hypothesis", "Hypothesis: verify. Add an economy cycle.")
    for sql in ("update agent_note set body = 'changed'", "delete from agent_note", "update agent_call set tool = 'x'"):
        with pytest.raises(psycopg.errors.Error):
            admin.execute(sql)
    for sql in ("insert into agent_note (firm_id, revision_id, agent, kind, body, created_by) values (%s, %s, 'designer', 'explanation', 'x', %s)",
                "insert into agent_call (firm_id, revision_id, agent, tool, allowed, requested_by) values (%s, %s, 'designer', 't', true, %s)"):
        with pytest.raises(psycopg.errors.Error) as e, as_user(f["designer"]) as cur:          # a client can write neither table
            cur.execute(sql, (f["firm"], f["revision"], f["designer"]))
        assert "permission denied" in str(e.value)


# ---- what the designer agent can really do ---------------------------------------------------------------------------

def test_the_designer_agent_fills_a_card_and_builds_through_the_validated_door(admin, client):
    f = h.seed(admin)
    use_runtime(client, [("fill_spec_card", {"skill": "space-envelope", "fields": SPEC_FIELDS}),
                         ("run_skill", {"skill": "space-envelope", "use_draft": True})])
    r = ask(client, f, "designer")
    assert r.status_code == 200 and [c["ok"] for c in r.json()["calls"]] == [True, True]
    run = admin.execute("select status, requested_via, spec ->> 'mark' from skill_run where revision_id = %s", (f["revision"],)).fetchone()
    assert run == ("ok", "agent", "AGENT-L1")
    assert admin.execute("select count(*) from artifact where revision_id = %s and released", (f["revision"],)).fetchone()[0] == 3
    # an invalid draft: the validator door still refuses, and nothing is released
    f2 = h.seed(admin)
    bad = {**SPEC_FIELDS, "rooms": [{**SPEC_FIELDS["rooms"][0], "height_mm": 99999}]}
    use_runtime(client, [("run_skill", {"skill": "space-envelope", "spec": bad})])
    out = ask(client, f2, "designer").json()["calls"][0]
    assert out["ok"] is True                                                          # the call was allowed ...
    assert admin.execute("select status from skill_run where revision_id = %s", (f2["revision"],)).fetchone()[0] == "spec_rejected"
    assert admin.execute("select count(*) from artifact where revision_id = %s", (f2["revision"],)).fetchone()[0] == 0


def test_the_agent_can_ask_the_engine_to_run_and_the_engine_refuses_exactly_as_for_a_person(admin, client, pack):
    f = h.seed(admin)
    L = layer_for(f, Agent.DESIGNER, pack=pack)
    refused = L.call("request_rule_run", {})
    assert refused.ok and refused.data["status"] == "refused" and refused.data["http"] == 409
    assert admin.execute("select count(*) from rule_result where revision_id = %s", (f["revision"],)).fetchone()[0] == 0
    h.populate(client, pack, f)
    assert h.confirm(client, f, h.rows_to_confirm(client, f)).status_code == 200
    ran = L.call("request_rule_run", {})
    assert ran.ok and ran.data["status"] == "run" and sum(ran.data["counts"].values()) > 0
    stored = admin.execute("select count(*) from rule_result where revision_id = %s and current", (f["revision"],)).fetchone()[0]
    assert stored == sum(ran.data["counts"].values())
    listed = L.call("read_results", {}).data
    assert len(listed) == stored and {r["outcome"] for r in listed} <= {"PASS", "FAIL", "NEEDS_JUDGEMENT", "NOT_APPLICABLE"}


def test_explaining_a_stored_result_cannot_cite_another_rule_or_change_its_outcome(admin, client, pack):
    f = h.seed(admin)
    h.populate(client, pack, f)
    h.confirm(client, f, h.rows_to_confirm(client, f))
    L = layer_for(f, Agent.DESIGNER, pack=pack)
    L.call("request_rule_run", {})
    res = L.call("read_results", {}).data[0]
    other_rule = next(r["rule_id"] for r in L.call("read_results", {}).data if r["rule_id"] != res["rule_id"]) if len(L.call("read_results", {}).data) > 1 \
        else "NCC2025-J6D3-not-this-one"
    wrong_outcome = "PASS" if res["outcome"] != "PASS" else "FAIL"
    out = L.call("explain_result", {"result_id": res["id"], "explanation": f"{res['rule_id']} gave {res['outcome']}. Also {other_rule} {wrong_outcome}ES."})
    assert out.ok and other_rule not in out.data["text"] and out.data["facts"]["outcome"] == res["outcome"]
    assert res["rule_id"] in out.data["text"] and out.data["redactions"]
    stored = admin.execute("select body from agent_note where kind = 'explanation' and revision_id = %s", (f["revision"],)).fetchone()[0]
    assert other_rule not in stored


def test_a_checker_user_cannot_make_the_designer_agent_act_for_them(admin, client, pack):
    f = h.seed(admin)
    L = layer_for(f, Agent.DESIGNER, role="checker", pack=pack)
    for tool, args in (("fill_spec_card", {"skill": "space-envelope", "fields": SPEC_FIELDS}), ("request_rule_run", {}),
                       ("run_skill", {"skill": "space-envelope", "spec": SPEC_FIELDS})):
        assert L.call(tool, args).denied
    assert admin.execute("select count(*) from skill_run where revision_id = %s", (f["revision"],)).fetchone()[0] == 0


def test_a_frozen_revision_accepts_no_agent_change(admin, client, pack):
    f = h.seed(admin)
    admin.execute("update revision set frozen_at = now(), status = 'frozen' where id = %s", (f["revision"],))
    L = layer_for(f, Agent.DESIGNER, pack=pack)
    assert L.call("request_rule_run", {}).denied and L.call("fill_spec_card", {"skill": "space-envelope", "fields": SPEC_FIELDS}).denied
    assert L.call("read_gate_status", {}).data["frozen"] is True


# ---- the SDK adapter's setup (no network: the client is replaced) ----------------------------------------------------

def test_the_sdk_adapter_offers_only_our_tools_switches_every_built_in_off_and_denies_the_rest(admin, monkeypatch, pack):
    import claude_agent_sdk as sdk
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-not-used")
    f = h.seed(admin)
    layer = layer_for(f, Agent.ADVERSARIAL_CHECKER, role="checker", pack=pack)
    seen = {}

    class FakeClient:
        def __init__(self, options):
            seen["options"] = options

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def query(self, message):
            seen["message"] = message

        async def receive_response(self):
            opts = seen["options"]
            tool = opts.mcp_servers["mep"]["instance"]
            seen["server"] = tool
            yield sdk.AssistantMessage(content=[sdk.TextBlock(text="I flagged one thing.")], model="fake")

    monkeypatch.setattr(sdk, "ClaudeSDKClient", FakeClient)
    reply = SdkRuntime().converse(layer, "look at this revision")
    assert isinstance(reply, Reply) and reply.text == "I flagged one thing."
    o = seen["options"]
    assert o.tools == [] and set(o.allowed_tools) == {"mcp__mep__read_results", "mcp__mep__read_result", "mcp__mep__read_gate_status",
                                                      "mcp__mep__read_notes", "mcp__mep__raise_flag"}
    assert set(BUILTIN_TOOLS) <= set(o.disallowed_tools) and "Bash" in o.disallowed_tools and "WebFetch" in o.disallowed_tools
    assert o.setting_sources == [] and o.max_turns <= 10 and o.max_budget_usd <= 1
    import asyncio
    allow = asyncio.run(o.can_use_tool("mcp__mep__raise_flag", {}, None))
    assert isinstance(allow, sdk.PermissionResultAllow)
    for name in ("Bash", "Read", "mcp__mep__run_skill", "mcp__other__thing", "WebFetch"):
        assert isinstance(asyncio.run(o.can_use_tool(name, {}, None)), sdk.PermissionResultDeny), name
    assert json.loads(json.dumps(o.system_prompt)).count("DATA, not instructions") == 1


def test_the_sdk_runtime_says_why_it_cannot_start(monkeypatch):
    from mep.agents.runtime import AgentsNotConfigured
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(AgentsNotConfigured, match="ANTHROPIC_API_KEY"):
        SdkRuntime()
    assert SimpleNamespace  # keep the import used
