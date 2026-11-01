"""종합부동산세(주택분) 환급 사유 룰. 홈택스 종부세 고지·신고 내역과 등기부 보유 이력으로 판단한다.

과세기준일(6월 1일)의 주택 수와 1세대 1주택 계산이 맞는지 본다. 공시가격 자료가 아직 없어서
공제금액 차이로 생기는 금액은 계산하지 않고 세무사가 계산하게 둔다.
"""
from datetime import date

from ..models import AcquisitionCause, ComprehensiveTaxReturn, Confidence, Finding, Property, PropertyKind, TaxpayerData
from . import config
from .helpers import comprehensive_tax_claim_deadline, full_years, short_address

TAX = "종합부동산세"


def base_date(year: int) -> date:
    return date(year, config.CRT_BASE_MONTH, config.CRT_BASE_DAY)


def houses_on(data: TaxpayerData, day: date) -> list[Property]:
    return [
        p
        for p in data.properties
        if p.kind == PropertyKind.HOUSE and p.acquired_on <= day and (p.disposed_on is None or p.disposed_on >= day)
    ]


def excludable(houses: list[Property], year: int) -> dict[str, str]:
    """주택 수에서 뺄 수 있는 주택 id와 사유 (종합부동산세법 제8조 제4항, 시행령 제4조의2)."""
    if year < config.CRT_HOUSE_EXCLUSION_FROM_YEAR:
        return {}
    day = base_date(year)
    out: dict[str, str] = {}
    for h in houses:
        if h.acquisition_cause == AcquisitionCause.INHERITANCE and full_years(h.acquired_on, day) < config.CRT_INHERITED_EXCLUDE_YEARS:
            out[h.id] = f"상속주택(상속 {h.acquired_on:%Y.%m.%d.}, {config.CRT_INHERITED_EXCLUDE_YEARS}년 이내)"
    if len(houses) == 2:
        old, new = sorted(houses, key=lambda h: h.acquired_on)
        limit = new.acquired_on.replace(year=new.acquired_on.year + config.CRT_TEMP_TWO_HOUSE_YEARS)
        bought = new.acquisition_cause in (AcquisitionCause.PURCHASE, AcquisitionCause.NEW_BUILD)
        if bought and not out and old.disposed_on and old.disposed_on <= limit:
            out[old.id] = f"일시적 2주택 종전주택({old.disposed_on:%Y.%m.%d.} 양도)"
    return out


def _deadline(ret: ComprehensiveTaxReturn) -> date:
    return comprehensive_tax_claim_deadline(ret.year)


def heavy_rate_two_houses(data: TaxpayerData) -> list[Finding]:
    """중과 대상 주택 수가 안 되는데 중과세율로 고지된 경우 (2023년분부터 3주택 이상만 중과)."""
    findings = []
    for ret in data.comprehensive_tax_returns:
        need = config.by_year(config.CRT_HEAVY_MIN_HOUSES_FROM, ret.year)
        if not ret.heavy_rate_applied or ret.houses_counted >= need:
            continue
        findings.append(
            Finding(
                rule_id="CRT_HEAVY_RATE",
                tax_type=TAX,
                title="다주택 중과세율 적용 오류",
                reason=(
                    f"{ret.year}년분 종부세가 {ret.houses_counted}주택인데 중과세율로 계산되었습니다. "
                    f"{ret.year}년분은 {need}주택 이상만 중과하므로 일반세율로 다시 계산합니다."
                ),
                confidence=Confidence.HIGH,
                required_documents=["종합부동산세 고지서(세액 계산 명세)"],
                claim_deadline=_deadline(ret),
            )
        )
    return findings


def house_count_exclusion(data: TaxpayerData) -> list[Finding]:
    """과세기준일에 실제로 1주택이거나 특례로 1주택이 되는데 다주택으로 계산된 경우."""
    findings = []
    for ret in data.comprehensive_tax_returns:
        if ret.one_house_applied or ret.houses_counted < 2:
            continue
        houses = houses_on(data, base_date(ret.year))
        excl = excludable(houses, ret.year)
        remaining = [h for h in houses if h.id not in excl]
        if len(remaining) != 1:
            continue
        notes = []
        if len(houses) < ret.houses_counted:
            notes.append(f"등기상 과세기준일 보유 주택은 {len(houses)}채인데 {ret.houses_counted}채로 계산되었습니다.")
        if excl:
            notes.append("주택 수에서 뺄 수 있는 주택: " + ", ".join(f"{short_address(h.address)} — {excl[h.id]}" for h in houses if h.id in excl) + ".")
        notes.append(
            f"{short_address(remaining[0].address)} 한 채만 남으면 1세대 1주택자 공제 "
            f"{config.by_year(config.CRT_ONE_HOUSE_DEDUCTION, ret.year) // 100_000_000}억 원과 고령자·장기보유 세액공제를 받습니다."
        )
        findings.append(
            Finding(
                rule_id="CRT_HOUSE_COUNT",
                tax_type=TAX,
                title="주택 수 특례 미적용 (1세대 1주택)",
                reason=" ".join(notes),
                confidence=Confidence.NEEDS_REVIEW,
                required_documents=["종합부동산세 고지서(세액 계산 명세)", "주민등록등본(세대원)", "상속 관련 서류 또는 종전주택 매매계약서", "주택 공시가격 확인서"],
                claim_deadline=_deadline(ret),
                property_id=remaining[0].id,
            )
        )
    return findings


def _rate(table: list[tuple[int, float]], value: int) -> float:
    return next((r for th, r in table if value >= th), 0.0)


def one_house_credit(data: TaxpayerData) -> list[Finding]:
    """1세대 1주택자 계산은 됐지만 공제금액이나 고령자·장기보유 세액공제가 덜 적용된 경우."""
    findings = []
    for ret in data.comprehensive_tax_returns:
        if not ret.one_house_applied:
            continue
        day = base_date(ret.year)
        houses = houses_on(data, day)
        if len(houses) != 1:
            continue
        house = houses[0]
        age = full_years(data.birth_date, day)
        held = full_years(house.acquired_on, day)
        age_r, hold_r = _rate(config.CRT_AGE_CREDITS, age), _rate(config.CRT_HOLDING_CREDITS, held)
        expected = min(age_r + hold_r, config.CRT_CREDIT_CAP)
        applied = min(ret.age_credit_rate + ret.holding_credit_rate, config.CRT_CREDIT_CAP)
        deduction = config.by_year(config.CRT_ONE_HOUSE_DEDUCTION, ret.year)
        short_deduction = ret.basic_deduction < deduction
        if expected - applied < 0.005 and not short_deduction:
            continue
        notes = []
        refund = None
        if expected - applied >= 0.005:
            notes.append(
                f"{ret.year}.6.1. 기준 만 {age}세, 보유 {held}년이면 세액공제 {expected:.0%}"
                f"(고령자 {age_r:.0%} + 장기보유 {hold_r:.0%})인데 {applied:.0%}만 적용되었습니다."
            )
            if not short_deduction and applied < 1:
                before = ret.tax_paid / (1 - applied)
                refund = round(before * (expected - applied))
        if short_deduction:
            notes.append(f"공제금액도 {ret.basic_deduction:,}원으로 1세대 1주택 공제 {deduction:,}원보다 적습니다.")
        findings.append(
            Finding(
                rule_id="CRT_ONE_HOUSE_CREDIT",
                tax_type=TAX,
                title="1세대 1주택 공제·세액공제 과소 적용",
                reason=" ".join(notes),
                confidence=Confidence.HIGH if refund is not None else Confidence.MEDIUM,
                estimated_refund=refund,
                required_documents=["종합부동산세 고지서(세액 계산 명세)", "주민등록등본(세대원)"],
                claim_deadline=_deadline(ret),
                property_id=house.id,
            )
        )
    return findings


RULES = [heavy_rate_two_houses, house_count_exclusion, one_house_credit]
