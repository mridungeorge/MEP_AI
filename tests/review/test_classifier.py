"""The exception classifier: every way a result can need a human, and the one way it does not."""
import pytest
from mep.review.classifier import CLASSES, CLEAN, EXCEPTIONS, classify, classify_all

OK = {"subject_id": "ahu-1", "rule_id": "R1", "outcome": "PASS", "causes": [], "near_miss": {"is_near_miss": False},
      "stale": False, "inputs_used": {"x": {"value": 1, "provenance": "engineer_confirmed"}}}


def r(**kw):
    return {**OK, **kw}


def test_a_plain_confirmed_pass_is_the_only_clean_class():
    c = classify(OK)
    assert c.klass == CLEAN and c.is_clean and c.reasons == ()
    assert CLEAN not in EXCEPTIONS and CLASSES[-1] == CLEAN


@pytest.mark.parametrize("change,klass", [
    ({"outcome": "FAIL"}, "fail"),
    ({"outcome": "NEEDS_JUDGEMENT"}, "needs_judgement"),
    ({"outcome": "NOT_APPLICABLE"}, "not_applicable"),
    ({"outcome": "SOMETHING_NEW"}, "needs_judgement"),
    ({"outcome": None}, "needs_judgement"),
    ({"stale": True}, "stale"),
    ({"near_miss": {"is_near_miss": True}}, "near_miss"),
    ({"near_miss": "garbage"}, "near_miss"),
    ({"causes": [{"kind": "x"}]}, "needs_judgement"),
    ({"inputs_used": {"x": {"provenance": "address_lookup_confirmed"}}}, "lookup_input"),
])
def test_anything_unusual_is_an_exception(change, klass):
    c = classify(r(**change))
    assert c.klass == klass and not c.is_clean and c.reasons


def test_the_most_serious_reason_names_the_class_and_every_reason_is_listed():
    c = classify(r(outcome="FAIL", stale=True, near_miss={"is_near_miss": True}))
    assert c.klass == "fail" and len(c.reasons) == 3


def test_a_result_that_differs_from_its_parent_is_flipped_even_when_it_passes():
    assert classify(OK, parent={"outcome": "FAIL"}).klass == "flipped"
    assert classify(OK, parent={"outcome": "PASS"}).is_clean
    assert classify(OK, parent=None).is_clean
    assert classify(r(outcome="FAIL"), parent={"outcome": "PASS"}).klass == "fail"        # fail still outranks flipped


def test_database_shaped_rows_are_read_too():
    row = {"subject_id": "s", "rule_id": "R", "result": "PASS", "inputs": {}, "causes": [], "near_miss": None, "stale": False}
    assert classify(row).is_clean


def test_classify_all_pairs_each_result_with_its_parent():
    rows = [r(subject_id="a"), r(subject_id="b")]
    out = classify_all(rows, {("b", "R1"): {"outcome": "FAIL"}})
    assert out[("a", "R1")].is_clean and out[("b", "R1")].klass == "flipped"
