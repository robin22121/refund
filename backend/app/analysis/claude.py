"""Claude로 환급 전략 초안을 쓴다.

구조화 출력(JSON 스키마)으로 StrategyOutput을 바로 받는다. 거절(refusal) 시에는
서버 측 폴백으로 다른 모델이 이어 받는다.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone

from .base import AnalysisInput
from .legal import RULE_PLAYBOOK
from .models import StrategyDraft, StrategyOutput

DEFAULT_MODEL = "claude-opus-5-5"

SYSTEM_PROMPT = """\
당신은 부동산 세금(양도소득세·취득세·종합부동산세) 경정청구를 준비하는 세무 분석가입니다.
제휴 세무사가 검토할 환급 전략 초안을 씁니다. 초안은 세무사가 고치고 승인한 뒤에만 세무서에 제출됩니다.

입력:
- 룰 엔진 진단 결과(findings): 기계적으로 찾은 환급 가능 항목
- 수집 자료: 홈택스 양도소득세 신고 내역, 위택스 지방세 납부 내역, 등기부·대장·토지이용계획에서 얻은 부동산 정보, 주민등록초본 전입 이력
- 고객이 적은 검토 희망 항목(concern): 고객이 가장 아깝다고 느끼는 부분
- 룰별 참고 자료(playbook): 근거 조문, 예상 반론, 고객 질문 예시

할 일:
1. findings마다 청구 전략을 씁니다. rule_id와 property_id는 입력 값을 그대로 씁니다.
2. 자료에서 룰이 놓친 쟁점이 보이면 rule_id를 null로 두고 추가합니다. 근거가 약하면 추가하지 않습니다.
3. 고객의 검토 희망 항목과 관련된 쟁점은 근거가 있는 한 우선순위를 앞에 둡니다.
4. 금액은 자료로 계산할 수 있을 때만 적고, 아니면 null로 둡니다. 숫자를 지어내지 않습니다.
5. 사실관계가 자료로 확정되지 않으면 단정하지 말고 customer_questions와 evidence_needed에 적습니다.
6. 과세관청의 반론과 불인용 위험을 risks에 솔직하게 적습니다. 청구 실익이 없어 보이면 그렇게 씁니다.
7. claim_statement는 경정청구서 '청구 이유'란에 붙일 공문체 초안입니다. 채택될 쟁점만 번호를 매겨 씁니다.
8. customer_summary는 60~70대 고객이 읽습니다. 전문용어 없이 짧은 존댓말로, 환급을 장담하지 않습니다.

모든 문장은 한국어로 씁니다.
"""


def _strict(schema: dict) -> dict:
    """pydantic 스키마를 구조화 출력의 strict 형태로 바꾼다 (모든 필드 required, 추가 필드 금지)."""
    if isinstance(schema, dict):
        schema = {k: _strict(v) for k, v in schema.items() if k not in ("default", "title")}
        if schema.get("type") == "object" and "properties" in schema:
            schema["additionalProperties"] = False
            schema["required"] = list(schema["properties"])
    elif isinstance(schema, list):
        schema = [_strict(v) for v in schema]
    return schema


OUTPUT_SCHEMA = _strict(StrategyOutput.model_json_schema())


def build_user_message(inp: AnalysisInput) -> str:
    data = inp.data.model_dump(mode="json", exclude={"name", "birth_date"}) if inp.data else None
    playbook = {f.rule_id: RULE_PLAYBOOK.get(f.rule_id) for f in inp.report.findings}
    payload = {
        "concern": inp.concern,
        "concern_rule_ids": inp.concern_rule_ids,
        "findings": [f.model_dump(mode="json") for f in inp.report.findings],
        "documents": [
            {"address": a.address, "property_id": a.property.id if a.property else None, "documents": a.documents}
            for a in inp.assets
        ],
        "collected_data": data,
        "birth_year": inp.data.birth_date.year if inp.data else None,
        "playbook": playbook,
    }
    return (
        "아래 고객 자료로 환급 전략 초안을 써 주세요.\n\n<case>\n"
        + json.dumps(payload, ensure_ascii=False, indent=1)
        + "\n</case>"
    )


class ClaudeAnalyst:
    def __init__(self, model: str | None = None, client=None) -> None:
        self.model = model or os.getenv("ANALYST_MODEL", DEFAULT_MODEL)
        if client is None:
            import anthropic

            client = anthropic.Anthropic()
        self.client = client

    def draft(self, inp: AnalysisInput) -> StrategyDraft:
        response = self.client.beta.messages.create(
            model=self.model,
            max_tokens=16000,
            system=SYSTEM_PROMPT,
            thinking={"type": "adaptive"},
            output_config={"effort": "high", "format": {"type": "json_schema", "schema": OUTPUT_SCHEMA}},
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            messages=[{"role": "user", "content": build_user_message(inp)}],
        )
        if response.stop_reason == "refusal":
            raise AnalysisError("AI가 이 건의 분석을 거절했습니다. 세무사가 직접 검토해 주세요.")
        if response.stop_reason == "max_tokens":
            raise AnalysisError("AI 응답이 길이 제한에 걸려 잘렸습니다. 다시 시도해 주세요.")
        text = "".join(b.text for b in response.content if b.type == "text")
        out = StrategyOutput.model_validate_json(text)
        return StrategyDraft(**out.model_dump(), model=response.model, generated_at=datetime.now(timezone.utc))


class AnalysisError(RuntimeError):
    pass
