from datetime import date, timedelta

from ..models import AcquisitionCause, PropertyKind, Residence, TaxpayerData
from . import config


def full_years(start: date, end: date) -> int:
    years = end.year - start.year
    if (end.month, end.day) < (start.month, start.day):
        years -= 1
    return max(years, 0)


def residence_years(residences: list[Residence], property_id: str, until: date) -> int:
    """해당 부동산에 전입해 산 기간을 만 년수로 합산한다."""
    days = 0
    for r in residences:
        if r.property_id != property_id:
            continue
        end = min(r.moved_out or until, until)
        if end > r.moved_in:
            days += (end - r.moved_in).days
    return days // 365


def houses_owned_on(data: TaxpayerData, day: date) -> int:
    return sum(
        1
        for p in data.properties
        if p.kind == PropertyKind.HOUSE
        and p.acquired_on <= day
        and (p.disposed_on is None or p.disposed_on >= day)
    )


def capital_gains_claim_deadline(transfer_date: date) -> date:
    """확정신고기한(양도일 다음 해 5월 31일)부터 5년."""
    return date(transfer_date.year + 1 + config.CLAIM_PERIOD_YEARS, 5, 31)


def _month_end(year: int, month: int) -> date:
    """month가 12를 넘으면 다음 해로 넘긴다."""
    year, month = year + (month - 1) // 12, (month - 1) % 12 + 1
    nxt = date(year + month // 12, month % 12 + 1, 1)
    return date.fromordinal(nxt.toordinal() - 1)


def _plus_years(d: date, years: int) -> date:
    try:
        return d.replace(year=d.year + years)
    except ValueError:  # 2월 29일
        return d.replace(year=d.year + years, day=28)


def acquisition_tax_filing_deadline(acquired_on: date, cause: AcquisitionCause) -> date:
    """취득세 법정신고기한 (지방세법 제20조 제1항)."""
    if cause == AcquisitionCause.INHERITANCE:
        return _month_end(acquired_on.year, acquired_on.month + config.ACQ_INHERITANCE_FILING_MONTHS)
    if cause == AcquisitionCause.GIFT and acquired_on >= config.ACQ_GIFT_RULE_FROM:
        return _month_end(acquired_on.year, acquired_on.month + config.ACQ_GIFT_FILING_MONTHS)
    return acquired_on + timedelta(days=config.ACQ_FILING_DAYS)


def acquisition_tax_claim_deadline(acquired_on: date, cause: AcquisitionCause) -> date:
    """취득세 경정청구 마감: 법정신고기한부터 5년 (지방세기본법 제50조 제1항)."""
    return _plus_years(acquisition_tax_filing_deadline(acquired_on, cause), config.LOCAL_CLAIM_PERIOD_YEARS)


def comprehensive_tax_claim_deadline(year: int) -> date:
    """종부세 경정청구 마감: 그해 12월 15일 납부기한부터 5년."""
    return date(year + config.CRT_CLAIM_PERIOD_YEARS, config.CRT_DUE_MONTH, config.CRT_DUE_DAY)


def house_general_rate(price: int) -> float:
    """유상거래 주택 표준세율 (지방세법 제11조 제1항 제8호)."""
    if price <= config.HOUSE_RATE_LOW_LIMIT:
        return config.HOUSE_RATE_LOW
    if price > config.HOUSE_RATE_HIGH_LIMIT:
        return config.HOUSE_RATE_HIGH
    # (취득가액 × 2/3억 − 3) × 1/100, 소수점 다섯째 자리에서 반올림
    return round((price * 2 / 300_000_000 - 3) / 100, 5)


def short_address(addr: str) -> str:
    """동·호수와 괄호 부분을 뺀 짧은 주소."""
    return addr.split(" (")[0].split(",")[0]
