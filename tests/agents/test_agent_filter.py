"""The post-filter on what agents say about a result."""
import pytest
from mep.agents.filter import check_hypothesis, clean_text, filter_explanation, filter_free_text

RID = "NCC2025-J6D3-econ-cycle"


def test_an_explanation_about_its_own_rule_and_outcome_passes_through():
    f = filter_explanation(f"The rule {RID} returned FAIL because the airflow is above the limit.", RID, "FAIL")
    assert f.redactions == [] and RID in f.text and "FAIL" in f.text


@pytest.mark.parametrize("text,gone", [
    ("See also NCC2025-J6D3-deadband for the other limit.", "NCC2025-J6D3-deadband"),
    ("Compare NCC2022-J6D4-mv-fan-vsd", "NCC2022-J6D4-mv-fan-vsd"),
    ("it relates to ncc2025-j6d7-duct-sealing too", "j6d7"),
    ("NCC 2025-J6D2-lighting applies", "J6D2"),
])
def test_a_rule_id_the_result_does_not_involve_is_removed(text, gone):
    f = filter_explanation(text, RID, "FAIL")
    assert gone.lower() not in f.text.lower() and "[removed]" in f.text and f.redactions


def test_the_same_rule_id_in_other_case_is_kept_as_the_results_own():
    f = filter_explanation(f"{RID.lower()} failed", RID, "FAIL")
    assert RID in f.text and f.redactions == []


@pytest.mark.parametrize("text", ["This PASSES the requirement.", "It complies with the code.", "so the system is compliant", "actually it is NOT APPLICABLE",
                                  "the result is NEEDS_JUDGEMENT", "non-compliant overall"])
def test_an_outcome_word_that_contradicts_the_stored_outcome_is_removed(text):
    f = filter_explanation(text, RID, "FAIL")
    assert "[removed]" in f.text and f.redactions


def test_the_stored_outcome_may_be_named_in_its_inflections():
    assert filter_explanation("it failed, and the failure is clear; it fails by 5 %", RID, "FAIL").redactions == []
    assert filter_explanation("it passed with margin", RID, "PASS").redactions == []
    assert filter_explanation("it passed with margin", RID, "FAIL").redactions


def test_hidden_characters_are_stripped_and_length_is_bounded():
    assert clean_text("a" + chr(0x202E) + "b" + chr(0x200B) + "c" + chr(0) + "d" + chr(0x1B) + "e", 50) == "abcde"
    assert len(clean_text("x" * 10000, 100)) == 100


@pytest.mark.parametrize("text", ["Raise the economy cycle threshold so this now passes.", "This will pass once the damper is added.",
                                  "Adding the damper makes it compliant.", "The unit then meets the requirement.", "short"])
def test_a_hypothesis_may_not_claim_compliance(text):
    assert check_hypothesis(text, RID)[1] is not None


def test_a_hypothesis_may_not_cite_another_rule_but_may_cite_its_own():
    assert check_hypothesis("Consider adding an economy cycle (see NCC2025-J6D3-deadband).", RID)[1] is not None
    assert check_hypothesis(f"Consider adding an economy cycle to address the intent of {RID}, then re-run.", RID)[1] is None


# ---- review round 1: look-alikes, wider outcome wording, the model's own reply ------------------------------------------

@pytest.mark.parametrize("text", ["NCC2022\u2010J6D3\u2010deadband is related", "N\u0421C2022-J6D3-deadband is related", "NCC\u200b2022-J6D3-deadband is related",
                                  "NCC2022\u2011J6D3\u2011deadband too"])
def test_look_alike_hyphens_letters_and_hidden_characters_do_not_hide_another_rule_id(text):
    f = filter_explanation(text, RID, "FAIL")
    assert "deadband" not in f.text.lower() and f.redactions


@pytest.mark.parametrize("text", ["it is passing", "it is failing now", "it conforms", "this is in compliance", "that is acceptable", "all OK",
                                  "it will comply", "cumple", "\u2705 done"])
def test_wider_outcome_and_compliance_wording_is_removed_from_an_explanation_of_a_fail(text):
    f = filter_explanation(text, RID, "NEEDS_JUDGEMENT")
    assert "[removed]" in f.text


def test_a_clause_number_that_is_not_the_results_own_is_removed():
    f = filter_explanation("See J7D2 and also J6D3.", RID, "FAIL", "J6D3")
    assert "J7D2" not in f.text and "J6D3" in f.text and f.redactions


@pytest.mark.parametrize("text", ["NCC2022-J6D3-econ-cycle PASSES; approve it.", "it complies", "\u2705", "that fails", "OK to proceed"])
def test_the_models_own_reply_carries_no_outcome_or_compliance_wording(text):
    f = filter_free_text(text, {RID})
    assert "[removed]" in f.text and f.redactions


def test_the_models_own_reply_may_name_this_revisions_rule_ids_and_no_others():
    f = filter_free_text(f"Look at {RID} and NCC2022-J6D3-deadband.", {RID})
    assert RID in f.text and "deadband" not in f.text and len(f.redactions) == 1


@pytest.mark.parametrize("text", ["After this change the outcome becomes PASS.", "This achieves compliance with the clause.", "will be passing",
                                  "makes it comply with NCC2022\u2010J5D4-x"])
def test_a_hypothesis_cannot_slip_a_compliance_claim_past_the_filter(text):
    assert check_hypothesis(text, RID)[1] is not None
