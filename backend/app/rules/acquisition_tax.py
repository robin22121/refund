"""취득세 환급 사유 룰. 위택스 취득세 신고 내역(과세표준·세율·감면)과 등기부 보유 이력으로 판단한다.

취득세는 지방세라 경정청구는 위택스로 내고, 기한은 지방세기본법 제50조(법정신고기한부터 5년)를 따른다.
취득세 본세만 계산한다. 함께 낸 지방교육세·농어촌특별세도 같이 줄어드는지는 세무사가 확인한다.
"""
from collections.abc import Iterator
from datetime import timedelta

from ..models import (
    AcquisitionCause,
    Confidence,
    Finding,
    LocalTaxPayment,
    Property,
    PropertyKind,
    TaxpayerData,
)
from . import config
from .helpers import acquisition_tax_claim_deadline, house_general_rate, short_address

TAX = "취득세"


def _house_payments(data: TaxpayerData) -> Iterator[tuple[LocalTaxPayment, Property]]:
    by_id = {p.id: p for p in data.properties}
    for pay in data.local_tax_payments:
        prop = by_id.get(pay.property_id or "")
        if pay.tax_type == TAX and prop is not None and prop.kind == PropertyKind.HOUSE:
            yield pay, prop


def other_houses_at(data: TaxpayerData, prop: Property) -> list[Property]:
    """prop을 취득한 날 이미 갖고 있던 다른 주택. 같은 날 판 집은 넣지 않는다."""
    return [
        p
        for p in data.properties
        if p.id != prop.id
        and p.kind == PropertyKind.HOUSE
        and p.acquired_on <= prop.acquired_on
        and (p.disposed_on is None or p.disposed_on > prop.acquired_on)
    ]


def _deadline(prop: Property):
    return acquisition_tax_claim_deadline(prop.acquired_on, prop.acquisition_cause)


def heavy_rate_house_count(data: TaxpayerData) -> list[Finding]:
    """다주택 중과세율로 냈지만 취득 당시 1주택이었거나 일시적 2주택인 경우."""
    findings = []
    for pay, prop in _house_payments(data):
        if prop.acquisition_cause != AcquisitionCause.PURCHASE:
            continue
        if pay.rate is None or pay.tax_base is None or pay.rate < config.ACQ_HEAVY_RATE_MIN:
            continue
        others = other_houses_at(data, prop)
        limit = prop.acquired_on.replace(year=prop.acquired_on.year + config.ACQ_TEMP_TWO_HOUSE_YEARS)
        if not others:
            why = "취득일에 갖고 있던 다른 주택이 등기상 없어 1주택 취득으로 보입니다."
        elif len(others) == 1 and others[0].disposed_on and others[0].disposed_on <= limit:
            why = (
                f"종전 주택({short_address(others[0].address)})을 {others[0].disposed_on:%Y.%m.%d.}에 팔아 "
                f"{config.ACQ_TEMP_TWO_HOUSE_YEARS}년 안에 처분한 일시적 2주택으로 보입니다."
            )
        else:
            continue
        general = house_general_rate(pay.tax_base)
        refund = pay.amount - round(pay.tax_base * general)
        if refund <= 0:
            continue
        findings.append(
            Finding(
                rule_id="ACQ_HEAVY_RATE",
                tax_type=TAX,
                title="주택 수 산정 오류로 중과세율 적용",
                reason=(
                    f"취득세가 중과세율 {pay.rate:.0%}로 신고되었습니다. {why} "
                    f"일반세율 {general:.2%}이면 취득세 본세가 약 {refund:,}원 줄어듭니다. "
                    "세대원 명의 주택, 분양권·입주권, 주거용 오피스텔이 주택 수에 들어가는지 확인합니다."
                ),
                confidence=Confidence.MEDIUM,
                estimated_refund=refund,
                required_documents=["취득세 신고서", "주민등록등본(세대원)", "세대원 전원 주택 보유 확인", "종전 주택 매매계약서·등기부"],
                claim_deadline=_deadline(prop),
                property_id=prop.id,
            )
        )
    return findings


def first_home_reduction(data: TaxpayerData) -> list[Finding]:
    """생애최초 주택 구입 감면을 받지 않은 경우."""
    findings = []
    for pay, prop in _house_payments(data):
        if prop.acquisition_cause != AcquisitionCause.PURCHASE or prop.acquired_on < config.FIRST_HOME_FROM:
            continue
        if pay.reduction > 0 or pay.tax_base is None or pay.tax_base > config.FIRST_HOME_PRICE_LIMIT:
            continue
        if pay.rate is not None and pay.rate >= config.ACQ_HEAVY_RATE_MIN:
            continue  # 중과 대상이면 다주택이므로 생애최초가 아니다
        if any(p.kind == PropertyKind.HOUSE and p.id != prop.id and p.acquired_on < prop.acquired_on for p in data.properties):
            continue
        refund = min(pay.amount, config.FIRST_HOME_CAP)
        moved_in = [r.moved_in for r in data.residences if r.property_id == prop.id]
        lived = bool(moved_in) and min(moved_in) <= prop.acquired_on + timedelta(days=90)
        findings.append(
            Finding(
                rule_id="ACQ_FIRST_HOME",
                tax_type=TAX,
                title="생애최초 주택 구입 감면 미적용",
                reason=(
                    f"취득가액 {pay.tax_base:,}원 주택을 감면 없이 신고했고, 등기상 그 전에 주택을 가진 적이 없습니다. "
                    f"생애최초 감면이면 최대 {config.FIRST_HOME_CAP:,}원을 돌려받을 수 있습니다. "
                    + ("취득 후 3개월 안에 전입한 기록이 있습니다." if lived else "다만 3개월 안 전입·3년 실거주 요건을 확인해야 합니다.")
                ),
                confidence=Confidence.NEEDS_REVIEW,
                estimated_refund=refund,
                required_documents=["취득세 신고서", "주민등록초본", "배우자 포함 주택 소유 이력 (무주택 확인)"],
                claim_deadline=_deadline(prop),
                property_id=prop.id,
            )
        )
    return findings


def inherited_one_house_rate(data: TaxpayerData) -> list[Finding]:
    """무주택자가 주택 한 채를 상속받았는데 특례세율(0.8%)이 아닌 2.8%로 낸 경우."""
    findings = []
    for pay, prop in _house_payments(data):
        if prop.acquisition_cause != AcquisitionCause.INHERITANCE:
            continue
        if pay.rate is None or pay.tax_base is None or pay.rate < config.INHERITANCE_RATE - 1e-6:
            continue
        if other_houses_at(data, prop):
            continue
        refund = pay.amount - round(pay.tax_base * config.INHERITED_ONE_HOUSE_RATE)
        if refund <= 0:
            continue
        findings.append(
            Finding(
                rule_id="ACQ_INHERITED_ONE_HOUSE",
                tax_type=TAX,
                title="1가구 1주택 상속 특례세율 미적용",
                reason=(
                    f"상속 주택 취득세가 {pay.rate:.1%}로 신고되었습니다. 상속 당시 다른 주택이 없으면 "
                    f"특례세율 {config.INHERITED_ONE_HOUSE_RATE:.1%}이 적용되어 약 {refund:,}원이 줄어듭니다. "
                    "1가구(세대원 전원) 기준과 공동상속 지분을 확인합니다."
                ),
                confidence=Confidence.MEDIUM,
                estimated_refund=refund,
                required_documents=["취득세 신고서", "주민등록등본(상속개시일 기준 세대원)", "가족관계증명서", "상속재산 분할협의서"],
                claim_deadline=_deadline(prop),
                property_id=prop.id,
            )
        )
    return findings


RULES = [heavy_rate_house_count, first_home_reduction, inherited_one_house_rate]
