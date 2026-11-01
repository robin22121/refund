"""양도소득세 환급 사유 룰. 1단계 문서의 룰 표와 같은 순서로 하나씩 늘려 간다."""
from collections.abc import Iterator

from ..models import (
    AcquisitionCause,
    CapitalGainsReturn,
    Confidence,
    Finding,
    Property,
    PropertyKind,
    TaxpayerData,
)
from . import config
from .helpers import (
    capital_gains_claim_deadline,
    full_years,
    houses_owned_on,
    residence_years,
)

TAX = "양도소득세"


def _pairs(data: TaxpayerData) -> Iterator[tuple[CapitalGainsReturn, Property]]:
    by_id = {p.id: p for p in data.properties}
    for ret in data.capital_gains_returns:
        prop = by_id.get(ret.property_id)
        if prop is not None:
            yield ret, prop


def one_house_exemption(data: TaxpayerData) -> list[Finding]:
    """1세대 1주택 비과세가 적용되지 않은 신고를 찾는다."""
    findings = []
    for ret, prop in _pairs(data):
        if prop.kind != PropertyKind.HOUSE or ret.exemption_applied:
            continue
        if houses_owned_on(data, ret.transfer_date) != 1:
            continue
        if full_years(prop.acquired_on, ret.transfer_date) < config.EXEMPT_MIN_HOLDING_YEARS:
            continue
        full = ret.transfer_price <= config.EXEMPT_PRICE_LIMIT
        findings.append(
            Finding(
                rule_id="CGT_ONE_HOUSE_EXEMPTION",
                tax_type=TAX,
                title="1세대 1주택 비과세 미적용",
                reason=(
                    f"양도일 기준 보유 주택이 1채이고 {config.EXEMPT_MIN_HOLDING_YEARS}년 이상 "
                    "보유했는데 비과세 없이 신고되었습니다. 조정대상지역 취득분은 거주요건도 확인해야 합니다."
                ),
                confidence=Confidence.MEDIUM,
                estimated_refund=ret.tax_paid if full else None,
                required_documents=["주민등록초본(세대원 포함)", "세대원 전원 주택 보유 확인"],
                claim_deadline=capital_gains_claim_deadline(ret.transfer_date),
                property_id=prop.id,
            )
        )
    return findings


def long_term_deduction_residence(data: TaxpayerData) -> list[Finding]:
    """1세대 1주택 고가주택에서 거주기간 공제가 덜 적용된 경우."""
    findings = []
    for ret, prop in _pairs(data):
        if prop.kind != PropertyKind.HOUSE or not ret.exemption_applied:
            continue
        if ret.transfer_price <= config.EXEMPT_PRICE_LIMIT:
            continue
        lived = residence_years(data.residences, prop.id, ret.transfer_date)
        if lived < config.LTHD_MIN_RESIDENCE_YEARS:
            continue
        held = full_years(prop.acquired_on, ret.transfer_date)
        expected = min(held * config.LTHD_HOLDING_RATE_PER_YEAR, config.LTHD_EACH_CAP) + min(
            lived * config.LTHD_RESIDENCE_RATE_PER_YEAR, config.LTHD_EACH_CAP
        )
        if expected - ret.long_term_deduction_rate < 0.005:
            continue
        findings.append(
            Finding(
                rule_id="CGT_LTHD_RESIDENCE",
                tax_type=TAX,
                title="장기보유특별공제 과소 적용",
                reason=(
                    f"보유 {held}년, 거주 {lived}년이면 공제율 {expected:.0%}가 예상되는데 "
                    f"{ret.long_term_deduction_rate:.0%}만 적용되었습니다."
                ),
                confidence=Confidence.HIGH,
                required_documents=["주민등록초본"],
                claim_deadline=capital_gains_claim_deadline(ret.transfer_date),
                property_id=prop.id,
            )
        )
    return findings


def missing_acquisition_tax_expense(data: TaxpayerData) -> list[Finding]:
    """위택스 취득세 납부액보다 필요경비가 적게 신고된 경우."""
    findings = []
    for ret, prop in _pairs(data):
        acq_tax = sum(
            p.amount
            for p in data.local_tax_payments
            if p.tax_type == "취득세" and p.property_id == prop.id
        )
        if acq_tax == 0 or ret.necessary_expenses >= acq_tax or ret.used_converted_price:
            # 환산취득가액을 쓰면 실제 필요경비 대신 개산공제가 적용되므로 제외
            continue
        findings.append(
            Finding(
                rule_id="CGT_MISSING_EXPENSE",
                tax_type=TAX,
                title="필요경비 누락 (취득세 등)",
                reason=(
                    f"취득세 {acq_tax:,}원을 납부했는데 신고된 필요경비는 "
                    f"{ret.necessary_expenses:,}원입니다. 중개수수료·법무사비·자본적 지출도 함께 확인합니다."
                ),
                confidence=Confidence.MEDIUM,
                required_documents=["취득세 영수증", "중개수수료·법무사비 영수증", "자본적 지출 증빙"],
                claim_deadline=capital_gains_claim_deadline(ret.transfer_date),
                property_id=prop.id,
            )
        )
    return findings


def non_business_land_review(data: TaxpayerData) -> list[Finding]:
    """비사업용토지 중과가 적용된 토지는 사업용 판정 요건을 다시 검토한다."""
    findings = []
    for ret, prop in _pairs(data):
        if prop.kind != PropertyKind.LAND or not ret.non_business_land_surcharge:
            continue
        lived_nearby = any(
            r.moved_in <= ret.transfer_date for r in data.residences if r.property_id == prop.id
        )
        findings.append(
            Finding(
                rule_id="CGT_NON_BUSINESS_LAND",
                tax_type=TAX,
                title="비사업용토지 판정 검토",
                reason=(
                    f"지목 {prop.land_category or '미확인'}, 용도지역 {prop.zoning or '미확인'} 토지에 "
                    "비사업용토지 중과가 적용되었습니다. 사업용 기간 기준, 재촌·자경, 부득이한 사유를 확인합니다."
                    + (" 소재지 거주 이력이 있습니다." if lived_nearby else "")
                ),
                confidence=Confidence.NEEDS_REVIEW,
                required_documents=["토지이용계획확인원", "토지대장(지목 변경 이력)", "주민등록초본", "농지 경작 증빙"],
                claim_deadline=capital_gains_claim_deadline(ret.transfer_date),
                property_id=prop.id,
            )
        )
    return findings


def converted_price_building_review(data: TaxpayerData) -> list[Finding]:
    """환산취득가액을 쓴 건물·토지는 안분과 가산세 적용을 확인한다."""
    findings = []
    for ret, prop in _pairs(data):
        if not ret.used_converted_price:
            continue
        notes = ["토지·건물 기준시가 안분과 취득 당시 기준시가 적용이 맞는지 확인합니다."]
        if (
            prop.acquisition_cause == AcquisitionCause.NEW_BUILD
            and prop.building_approved_on
            and full_years(prop.building_approved_on, ret.transfer_date)
            < config.CONVERTED_PRICE_PENALTY_YEARS
        ):
            notes.append("신축 후 5년 안에 양도해 환산가액 가산세 대상인지도 확인합니다.")
        if prop.registered_price:
            notes.append(f"등기부 거래가액 {prop.registered_price:,}원과 비교해 실지거래가액 적용이 유리한지 봅니다.")
        findings.append(
            Finding(
                rule_id="CGT_CONVERTED_PRICE",
                tax_type=TAX,
                title="환산취득가액 확인",
                reason=" ".join(notes),
                confidence=Confidence.NEEDS_REVIEW,
                required_documents=["건축물대장", "토지대장", "취득·양도 당시 기준시가 자료"],
                claim_deadline=capital_gains_claim_deadline(ret.transfer_date),
                property_id=prop.id,
            )
        )
    return findings


RULES = [
    one_house_exemption,
    long_term_deduction_residence,
    missing_acquisition_tax_expense,
    non_business_land_review,
    converted_price_building_review,
]
