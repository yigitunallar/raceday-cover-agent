"""The model's writing: number validation and the template fallback. No network, no key."""
import time
from datetime import datetime, timedelta, timezone

from raceday import rules
from raceday.agent import writer

NOW = datetime(2026, 11, 14, 12, 0, tzinfo=timezone.utc)
FLAT_HCAP = {"type": "Flat", "race_name": "Kilbarrow Handicap", "pattern": ""}


def day(n):
    return (NOW - timedelta(days=n)).date().isoformat()


BUSY = {"runs": [{"date": day(n), "finished": n != 4} for n in (27, 20, 13, 8, 4)],
        "breeding": {"risk_band": "A"},
        "health_records": [{"date": day(400), "kind": "tendon scan", "result": "issue", "pre_existing": True,
                            "condition": "left fore tendon"}]}


def busy_quote():
    return rules.quote(FLAT_HCAP, {"ofr": "84"}, 1.0, BUSY, add_ons=["vet_fees"], today=NOW)


# ---- number validation

def test_numbers_are_read_the_way_people_write_them():
    assert writer.numbers_in("EUR 1,234.50 on 60,000 (+20%), 450k, 3-year-old") == {1234.5, 60000, 20, 450000, 3}


def test_numbers_from_the_facts_pass():
    f = writer.price_facts(busy_quote())
    text = f"EUR {f['total_to_pay']} on the table default of {f['sum_insured']}, priced higher for 5 runs in 30 days."
    assert writer.check(text, f, 40) is None


def test_an_invented_number_fails():
    f = writer.price_facts(busy_quote())
    assert "999" in writer.check("You pay EUR 999 for this horse.", f, 40)


def test_rounded_or_abbreviated_numbers_fail_unless_they_are_in_the_facts():
    f = writer.price_facts(busy_quote())  # sum insured 60,000
    assert writer.check("Covers 60k.", f, 40) is None          # 60k == 60,000: same number
    assert writer.check("Covers about 61k.", f, 40) is not None


def test_too_long_or_empty_fails():
    f = writer.price_facts(busy_quote())
    assert writer.check("", f, 40) == "empty"
    assert "too long" in writer.check("word " * 41, f, 40)


# ---- fallback

def _reset_breaker():
    writer._down_until = 0.0


def test_model_text_is_used_when_it_passes():
    _reset_breaker()
    f_total = writer.price_facts(busy_quote())["total_to_pay"]
    out = writer.explain_price(busy_quote(), call=lambda s, f: f"EUR {f_total}, higher for 5 runs in 30 days.")
    assert out["source"] == "model" and f_total in out["text"]


def test_invented_number_falls_back_to_the_template():
    _reset_breaker()
    out = writer.explain_price(busy_quote(), call=lambda s, f: "Priced at EUR 123.45 because I said so.")
    assert out["source"] == "template"
    assert "failed validation" in out["fallback_reason"]
    assert writer.check(out["text"], out["facts"], 60) is None  # the template obeys the same rule


def test_model_error_falls_back_and_opens_the_breaker():
    _reset_breaker()

    def boom(s, f):
        raise ConnectionError("API down")

    out = writer.explain_price(busy_quote(), call=boom)
    assert out["source"] == "template" and "ConnectionError" in out["fallback_reason"]
    assert writer._down_until > time.monotonic()
    _reset_breaker()


def test_slow_model_times_out_to_the_template(monkeypatch):
    _reset_breaker()
    monkeypatch.setattr(writer, "TIMEOUT_S", 0.2)
    out = writer.explain_price(busy_quote(), call=lambda s, f: time.sleep(1) or "too late")
    assert out["source"] == "template" and "timed out" in out["fallback_reason"]
    _reset_breaker()


def test_no_key_uses_the_template_without_calling(monkeypatch):
    _reset_breaker()
    monkeypatch.setattr(writer.config, "ANTHROPIC_API_KEY", "")
    out = writer.explain_price(busy_quote())
    assert out["source"] == "template" and out["fallback_reason"] == "no API key"


def test_template_explanation_mentions_the_reasons():
    out = writer.explain_price(busy_quote(), call=lambda s, f: "")
    text = out["text"]
    assert "5 runs in 30 days" in text and "failed to finish" in text
    assert "left fore tendon" in text and "vet fees" in text and "Flat" in text


def test_referral_summary_falls_back_and_passes_its_own_check():
    _reset_breaker()
    q = rules.quote({"type": "Flat", "race_name": "Kilbarrow Stakes", "pattern": "Group 3"}, {"ofr": "104"}, 1.0,
                    {"sales": [{"date": day(400), "price": 62_000}], "career_earnings": "41000"},
                    stated_value=450_000, today=NOW)
    task = {"horse": "Northern Ledger", "race": {"race_name": "Kilbarrow Stakes"}, "quote": q,
            "stated_value": 450_000, "deadline": NOW + timedelta(hours=3, minutes=20)}
    profile = {"official_rating": "104", "declarations": [{"date": day(14), "fit": False, "note": "lame after work"}],
               "health_records": [], "runs": [{"date": day(35), "finished": True, "position": "2"}]}
    out = writer.summarize_referral(task, profile, "value above the insurer's limit", 200_000,
                                    call=lambda s, f: "Owner says 500,000.", now=NOW)
    assert out["source"] == "template"
    text = out["text"]
    assert "450,000" in text and "82,000" in text and "3 hours 20 minutes" in text and "lame after work" in text
    assert writer.check(text, out["facts"], writer.SUMMARY_MAX_WORDS) is None
