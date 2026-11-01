"""API 키 없이 쓰는 가짜 분석기. 룰 결과와 RULE_PLAYBOOK으로 초안 모양을 만든다."""
from datetime import datetime, timezone

from .base import AnalysisInput
from .legal import CHANNEL, RULE_PLAYBOOK, SOURCE_DOCUMENT
from .models import StrategyDraft, StrategyItem

_CONF_ORDER = {"높음": 0, "보통": 1, "세무사 검토 필요": 2}


class MockAnalyst:
    model = "mock"

    def draft(self, inp: AnalysisInput) -> StrategyDraft:
        wanted = set(inp.concern_rule_ids)
        findings = sorted(
            inp.report.findings,
            key=lambda f: (
                f.rule_id not in wanted,
                _CONF_ORDER.get(f.confidence.value, 3),
                -(f.estimated_refund or 0),
            ),
        )
        items = []
        for i, f in enumerate(findings, start=1):
            pb = RULE_PLAYBOOK.get(f.rule_id, {})
            items.append(
                StrategyItem(
                    rule_id=f.rule_id,
                    tax_type=f.tax_type,
                    property_id=f.property_id,
                    title=f.title,
                    priority=i,
                    claim_ground=f.reason,
                    legal_basis=pb.get("legal_basis", []),
                    evidence_on_hand=[d for a in inp.assets if a.property and a.property.id == f.property_id for d in a.documents]
                    + [SOURCE_DOCUMENT.get(f.tax_type, "홈택스 신고 내역")],
                    evidence_needed=f.required_documents,
                    risks=pb.get("risks", "세무사 검토가 필요합니다."),
                    customer_questions=pb.get("questions", []),
                    estimated_refund=f.estimated_refund,
                    claim_deadline=f.claim_deadline,
                )
            )
        total = sum(it.estimated_refund or 0 for it in items)
        name = inp.report.taxpayer_name
        customer = (
            f"{name}님 신고 자료에서 다시 따져 볼 부분을 {len(items)}가지 찾았습니다. "
            + (f"지금 계산으로는 약 {total // 10000:,}만 원을 돌려받을 수 있어 보입니다. " if total else "")
            + "세무사가 서류를 확인한 뒤 세무서에 대신 신청하고, 필요한 자료가 있으면 전화로 여쭙겠습니다."
            if items
            else f"{name}님 신고 자료에서는 환급 사유를 찾지 못했습니다. 세무사가 한 번 더 확인해 드립니다."
        )
        advisor = (
            f"검토 희망: {inp.concern or '없음'}. "
            + " / ".join(f"{it.priority}. {it.title}({it.tax_type})" for it in items)
            + ". 가짜 분석기 초안이므로 사실관계와 금액을 직접 확인하세요."
        )
        # 경정청구서는 세목별로 따로 내므로(국세 홈택스, 지방세 위택스) 청구 이유도 세목별로 나눈다
        blocks = []
        for tax in dict.fromkeys(it.tax_type for it in items):
            body = "\n\n".join(
                f"{it.priority}. {it.title}\n{it.claim_ground}\n근거: {', '.join(it.legal_basis) or '검토 필요'}"
                for it in items
                if it.tax_type == tax
            )
            blocks.append(f"[{tax} · {CHANNEL.get(tax, '홈택스')}]\n{body}")
        statement = "\n\n".join(blocks)
        return StrategyDraft(
            customer_summary=customer,
            advisor_summary=advisor,
            items=items,
            claim_statement=statement,
            model=self.model,
            generated_at=datetime.now(timezone.utc),
        )
