from collections.abc import Callable
from datetime import date

from ..models import DiagnosisReport, Finding, TaxpayerData
from . import acquisition_tax, capital_gains, comprehensive_tax

Rule = Callable[[TaxpayerData], list[Finding]]

ALL_RULES: list[Rule] = [*capital_gains.RULES, *acquisition_tax.RULES, *comprehensive_tax.RULES]


def run_diagnosis(data: TaxpayerData, today: date | None = None) -> DiagnosisReport:
    """모든 룰을 돌리고, 경정청구 기한이 지난 결과는 뺀다."""
    today = today or date.today()
    findings = [
        f
        for rule in ALL_RULES
        for f in rule(data)
        if f.claim_deadline is None or f.claim_deadline >= today
    ]
    findings.sort(key=lambda f: (f.claim_deadline or date.max))
    addresses = {p.id: p.address for p in data.properties}
    for f in findings:
        if f.property_id and f.property_address is None:
            f.property_address = addresses.get(f.property_id)
    return DiagnosisReport(taxpayer_name=data.name, findings=findings)
