from raceday.agent import decider

BASE = {"horse": "X", "step": "issue", "system": "platform", "ledger_state": "not_found",
        "charge_verified": True, "horse_has_left": False, "decisions_so_far": 0}


def facts(**k):
    f = {**BASE, **k}
    f["allowed_moves"] = decider.allowed_moves(f)
    return f


def test_moves_follow_the_rules():
    assert set(facts()["allowed_moves"]) == {"read_back_again", "escalate", "resend_same_id", "refund_and_escalate"}
    assert "resend_same_id" not in facts(horse_has_left=True)["allowed_moves"]
    assert "refund_and_escalate" not in facts(charge_verified=False)["allowed_moves"]
    assert set(facts(decisions_so_far=3)["allowed_moves"]) == {"escalate", "refund_and_escalate"}


def test_model_choice_must_be_on_the_menu():
    out = decider.decide(facts(charge_verified=False), call=lambda s, f: '{"move": "refund_and_escalate", "reason": "x"}')
    assert out["source"] == "template" and out["move"] in facts(charge_verified=False)["allowed_moves"]


def test_valid_model_choice_is_used():
    out = decider.decide(facts(), call=lambda s, f: 'Sure: {"move": "read_back_again", "reason": "check first"}')
    assert out == {**out, "move": "read_back_again", "source": "model"}


def test_model_down_gives_safe_default():
    def boom(s, f):
        raise RuntimeError("down")
    assert decider.decide(facts(), call=boom)["move"] == "resend_same_id"
    assert decider.decide(facts(decisions_so_far=3), call=boom)["move"] == "refund_and_escalate"
