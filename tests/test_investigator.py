from types import SimpleNamespace as NS

from raceday.agent import investigator

TASK = {"horse": "X", "race": {"race_name": "R", "type": "Flat"}, "runner": {"ofr": "80"}, "ownership_share": 1.0,
        "stated_value": 450000}
PROFILE = {"runs": [{"date": "2026-10-01", "finished": True}], "health_records": [], "declarations": [],
           "sales": [{"date": "2025-01-01", "price": 62000}], "career_earnings": "41000"}


def test_owner_figure_makes_sales_check_required():
    assert "get_sales_and_earnings" in investigator.required_checks(TASK)
    assert "get_sales_and_earnings" not in investigator.required_checks({**TASK, "stated_value": None})


class FakeClient:
    """Model that tries to price too early, gets refused, then checks the rest."""
    def __init__(self):
        self.turn = 0
        self.messages = self

    def create(self, **kw):
        self.turn += 1
        use = lambda name, inp: NS(type="tool_use", id=f"t{self.turn}{name}", name=name, input=inp)
        plan = {1: [use("get_race_history", {"why": "start"}), use("ready_to_price", {"summary": "early"})],
                2: [use("get_past_declarations", {"why": "d"}),
                    use("get_sales_and_earnings", {"why": "owner value high"})],
                3: [use("ready_to_price", {"summary": "sold for 62,000; owner says 450,000"})]}
        return NS(content=plan[self.turn])


def test_code_refuses_early_pricing_and_model_completes():
    out = investigator.investigate(TASK, PROFILE, client_factory=FakeClient)
    assert out["source"] == "model"
    assert any("refused by code" in s["finding"] for s in out["trail"])
    assert {s["tool"] for s in out["trail"]} >= investigator.required_checks(TASK)


def test_model_failure_falls_back_to_required_checks():
    def broken():
        raise RuntimeError("down")
    out = investigator.investigate(TASK, PROFILE, client_factory=broken)
    assert out["source"] == "template"
    assert {s["tool"] for s in out["trail"]} == investigator.required_checks(TASK)
