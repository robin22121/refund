from datetime import date

from ..models import Residence, TaxpayerData, PropertyKind
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
