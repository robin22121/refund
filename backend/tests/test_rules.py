from datetime import date

from app.collectors.mock import sample_taxpayer
from app.models import CapitalGainsReturn, Property, PropertyKind, Residence, TaxpayerData
from app.rules import run_diagnosis

TODAY = date(2026, 11, 1)


def ids(report):
    return {(f.rule_id, f.property_id) for f in report.findings}


def test_sample_hits_expected_rules():
    report = run_diagnosis(sample_taxpayer(), today=TODAY)
    assert ids(report) == {
        ("CGT_ONE_HOUSE_EXEMPTION", "apt-1"),
        ("CGT_MISSING_EXPENSE", "apt-1"),
        ("CGT_NON_BUSINESS_LAND", "land-1"),
        ("CGT_CONVERTED_PRICE", "bldg-1"),
        ("ACQ_HEAVY_RATE", "apt-2"),
        ("CRT_HOUSE_COUNT", "apt-1"),
    }


def test_one_house_exemption_full_refund_and_deadline():
    report = run_diagnosis(sample_taxpayer(), today=TODAY)
    f = next(f for f in report.findings if f.rule_id == "CGT_ONE_HOUSE_EXEMPTION")
    assert f.estimated_refund == 78_000_000
    assert f.claim_deadline == date(2029, 5, 31)


def test_new_build_within_five_years_mentions_penalty():
    report = run_diagnosis(sample_taxpayer(), today=TODAY)
    f = next(f for f in report.findings if f.rule_id == "CGT_CONVERTED_PRICE")
    assert "가산세" in f.reason


def test_expired_claims_are_dropped():
    report = run_diagnosis(sample_taxpayer(), today=date(2030, 6, 1))
    assert report.findings == []


def test_two_houses_no_exemption_finding():
    data = sample_taxpayer()
    data.properties[1].acquired_on = date(2020, 1, 1)
    report = run_diagnosis(data, today=TODAY)
    assert "CGT_ONE_HOUSE_EXEMPTION" not in {f.rule_id for f in report.findings}


def test_long_term_deduction_residence_underapplied():
    data = TaxpayerData(
        name="김예시",
        birth_date=date(1970, 1, 1),
        properties=[
            Property(id="h", kind=PropertyKind.HOUSE, address="서울", acquired_on=date(2013, 1, 1)),
        ],
        residences=[Residence(address="서울", moved_in=date(2013, 1, 1), property_id="h")],
        capital_gains_returns=[
            CapitalGainsReturn(
                property_id="h",
                transfer_date=date(2024, 3, 1),
                transfer_price=2_000_000_000,
                acquisition_price=800_000_000,
                exemption_applied=True,
                long_term_deduction_rate=0.40,
            )
        ],
    )
    report = run_diagnosis(data, today=TODAY)
    f = next(f for f in report.findings if f.rule_id == "CGT_LTHD_RESIDENCE")
    assert "80%" in f.reason


def test_every_rule_has_playbook_and_channel():
    from app.analysis.legal import CHANNEL, RULE_PLAYBOOK, SOURCE_DOCUMENT
    from app.rules import ALL_RULES
    from app.rules.engine import Rule  # noqa: F401
    import inspect, re

    ids_in_code = set()
    for rule in ALL_RULES:
        ids_in_code |= set(re.findall(r'rule_id="([A-Z_]+)"', inspect.getsource(rule)))
    assert ids_in_code and ids_in_code <= set(RULE_PLAYBOOK)
    taxes = {"양도소득세", "취득세", "종합부동산세"}
    assert taxes <= set(CHANNEL) and taxes <= set(SOURCE_DOCUMENT)
    assert CHANNEL["취득세"] == "위택스"
