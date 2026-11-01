from collections.abc import Callable
from datetime import date

from ..models import DiagnosisReport, Finding, TaxpayerData
from . import capital_gains

Rule = Callable[[TaxpayerData], list[Finding]]

ALL_RULES: list[Rule] = [*capital_gains.RULES]


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
    return DiagnosisReport(taxpayer_name=data.name, findings=findings)
