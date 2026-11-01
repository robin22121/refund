"""취득세·종부세 룰 테스트. 세목별 경정청구 기한 계산도 함께 본다."""
from datetime import date

from app.collectors.mock import sample_taxpayer
from app.models import (
    AcquisitionCause,
    ComprehensiveTaxReturn,
    LocalTaxPayment,
    Property,
    PropertyKind,
    Residence,
    TaxpayerData,
)
from app.rules import run_diagnosis
from app.rules.helpers import (
    acquisition_tax_claim_deadline,
    acquisition_tax_filing_deadline,
    comprehensive_tax_claim_deadline,
    house_general_rate,
)

TODAY = date(2026, 11, 1)


def _house(id, acquired, disposed=None, cause=AcquisitionCause.PURCHASE):
    return Property(id=id, kind=PropertyKind.HOUSE, address=f"{id} 주소", acquired_on=acquired,
                    disposed_on=disposed, acquisition_cause=cause)


def _find(data, rule_id):
    return [f for f in run_diagnosis(data, today=TODAY).findings if f.rule_id == rule_id]


# ---------- 기한 ----------

def test_acquisition_filing_deadline_by_cause():
    assert acquisition_tax_filing_deadline(date(2023, 10, 1), AcquisitionCause.PURCHASE) == date(2023, 11, 30)
    # 상속: 상속개시일이 속하는 달의 말일부터 6개월
    assert acquisition_tax_filing_deadline(date(2021, 3, 10), AcquisitionCause.INHERITANCE) == date(2021, 9, 30)
    assert acquisition_tax_filing_deadline(date(2023, 9, 5), AcquisitionCause.INHERITANCE) == date(2024, 3, 31)
    # 증여: 2023년부터 달의 말일부터 3개월, 그 전은 60일
    assert acquisition_tax_filing_deadline(date(2023, 11, 15), AcquisitionCause.GIFT) == date(2024, 2, 29)
    assert acquisition_tax_filing_deadline(date(2022, 11, 15), AcquisitionCause.GIFT) == date(2023, 1, 14)


def test_acquisition_claim_deadline_is_five_years_from_filing_deadline():
    assert acquisition_tax_claim_deadline(date(2023, 10, 1), AcquisitionCause.PURCHASE) == date(2028, 11, 30)
    # 신고기한이 2월 29일이면 5년 뒤는 2월 28일
    assert acquisition_tax_claim_deadline(date(2023, 11, 15), AcquisitionCause.GIFT) == date(2029, 2, 28)


def test_comprehensive_claim_deadline():
    assert comprehensive_tax_claim_deadline(2021) == date(2026, 12, 15)


def test_house_general_rate_brackets():
    assert house_general_rate(600_000_000) == 0.01
    assert house_general_rate(750_000_000) == 0.02
    assert house_general_rate(900_000_001) == 0.03


# ---------- 취득세 ----------

def test_sample_heavy_rate_refund_and_deadline():
    [f] = _find(sample_taxpayer(), "ACQ_HEAVY_RATE")
    assert f.tax_type == "취득세"
    assert f.estimated_refund == 56_000_000 - round(700_000_000 * 0.01667)
    assert f.claim_deadline == date(2028, 11, 30)


def test_heavy_rate_temporary_two_houses():
    data = TaxpayerData(
        name="가", birth_date=date(1960, 1, 1),
        properties=[_house("old", date(2015, 1, 1), disposed=date(2024, 6, 1)), _house("new", date(2023, 3, 1))],
        local_tax_payments=[LocalTaxPayment(tax_type="취득세", property_id="new", amount=40_000_000,
                                            paid_on=date(2023, 3, 20), tax_base=500_000_000, rate=0.08)],
    )
    [f] = _find(data, "ACQ_HEAVY_RATE")
    assert "일시적 2주택" in f.reason
    assert f.estimated_refund == 35_000_000


def test_heavy_rate_skipped_when_old_house_kept_too_long():
    data = TaxpayerData(
        name="가", birth_date=date(1960, 1, 1),
        properties=[_house("old", date(2015, 1, 1), disposed=date(2026, 6, 1)), _house("new", date(2023, 3, 1))],
        local_tax_payments=[LocalTaxPayment(tax_type="취득세", property_id="new", amount=40_000_000,
                                            paid_on=date(2023, 3, 20), tax_base=500_000_000, rate=0.08)],
    )
    assert _find(data, "ACQ_HEAVY_RATE") == []


def test_first_home_reduction():
    data = TaxpayerData(
        name="가", birth_date=date(1990, 1, 1),
        properties=[_house("h", date(2023, 5, 1))],
        residences=[Residence(address="h", moved_in=date(2023, 5, 20), property_id="h")],
        local_tax_payments=[LocalTaxPayment(tax_type="취득세", property_id="h", amount=4_000_000,
                                            paid_on=date(2023, 5, 20), tax_base=400_000_000, rate=0.01)],
    )
    [f] = _find(data, "ACQ_FIRST_HOME")
    assert f.estimated_refund == 2_000_000
    assert "3개월 안에 전입" in f.reason


def test_first_home_skipped_with_prior_house_or_reduction():
    prior = _house("p", date(2010, 1, 1), disposed=date(2020, 1, 1))
    pay = LocalTaxPayment(tax_type="취득세", property_id="h", amount=4_000_000, paid_on=date(2023, 5, 20),
                          tax_base=400_000_000, rate=0.01)
    data = TaxpayerData(name="가", birth_date=date(1990, 1, 1), properties=[prior, _house("h", date(2023, 5, 1))],
                        local_tax_payments=[pay])
    assert _find(data, "ACQ_FIRST_HOME") == []
    data.properties = [_house("h", date(2023, 5, 1))]
    data.local_tax_payments = [pay.model_copy(update={"reduction": 2_000_000})]
    assert _find(data, "ACQ_FIRST_HOME") == []


def test_inherited_one_house_rate():
    data = TaxpayerData(
        name="가", birth_date=date(1970, 1, 1),
        properties=[_house("h", date(2024, 2, 10), cause=AcquisitionCause.INHERITANCE)],
        local_tax_payments=[LocalTaxPayment(tax_type="취득세", property_id="h", amount=5_600_000,
                                            paid_on=date(2024, 8, 1), tax_base=200_000_000, rate=0.028)],
    )
    [f] = _find(data, "ACQ_INHERITED_ONE_HOUSE")
    assert f.estimated_refund == 5_600_000 - 1_600_000
    assert f.claim_deadline == date(2029, 8, 31)


def test_sample_inherited_house_not_flagged_because_owner_had_another_house():
    assert _find(sample_taxpayer(), "ACQ_INHERITED_ONE_HOUSE") == []


# ---------- 종부세 ----------

def test_sample_house_count_exclusion_only_from_2022():
    [f] = _find(sample_taxpayer(), "CRT_HOUSE_COUNT")
    assert f.claim_deadline == date(2027, 12, 15)  # 2022년분. 2021년분은 특례가 없던 해라 안 걸린다
    assert "상속주택" in f.reason


def test_heavy_rate_two_houses_from_2023():
    data = TaxpayerData(
        name="가", birth_date=date(1960, 1, 1),
        comprehensive_tax_returns=[
            ComprehensiveTaxReturn(year=2023, houses_counted=2, basic_deduction=900_000_000,
                                   heavy_rate_applied=True, tax_paid=3_000_000),
            ComprehensiveTaxReturn(year=2022, houses_counted=2, basic_deduction=600_000_000,
                                   heavy_rate_applied=True, tax_paid=3_000_000),
        ],
    )
    fs = _find(data, "CRT_HEAVY_RATE")
    assert [f.claim_deadline for f in fs] == [date(2028, 12, 15)]


def test_one_house_credit_shortfall():
    data = TaxpayerData(
        name="가", birth_date=date(1956, 3, 1),  # 2023.6.1. 만 67세 → 30%
        properties=[_house("h", date(2010, 1, 1))],  # 보유 13년 → 40%
        comprehensive_tax_returns=[
            ComprehensiveTaxReturn(year=2023, houses_counted=1, one_house_applied=True,
                                   basic_deduction=1_200_000_000, age_credit_rate=0.20,
                                   holding_credit_rate=0.40, tax_paid=1_200_000),
        ],
    )
    [f] = _find(data, "CRT_ONE_HOUSE_CREDIT")
    # 공제 전 세액 300만 원 × 10%p
    assert f.estimated_refund == 300_000
    assert f.confidence.value == "높음"


def test_temporary_two_houses_excluded_for_comprehensive_tax():
    data = TaxpayerData(
        name="가", birth_date=date(1960, 1, 1),
        properties=[_house("old", date(2010, 1, 1), disposed=date(2024, 3, 1)), _house("new", date(2022, 4, 1))],
        comprehensive_tax_returns=[
            ComprehensiveTaxReturn(year=2023, houses_counted=2, basic_deduction=900_000_000, tax_paid=900_000),
        ],
    )
    [f] = _find(data, "CRT_HOUSE_COUNT")
    assert "일시적 2주택" in f.reason and f.property_id == "new"
