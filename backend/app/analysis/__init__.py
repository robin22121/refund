import os

from .base import AnalysisInput, Analyst, reconcile
from .claude import AnalysisError
from .mock import MockAnalyst
from .models import StrategyDraft, StrategyItem


def get_analyst() -> Analyst:
    """ANALYST=claude|mock. 지정하지 않으면 ANTHROPIC_API_KEY가 있을 때 Claude를 쓴다."""
    choice = os.getenv("ANALYST") or ("claude" if os.getenv("ANTHROPIC_API_KEY") else "mock")
    if choice == "claude":
        from .claude import ClaudeAnalyst

        return ClaudeAnalyst()
    return MockAnalyst()


def analyze(analyst: Analyst, inp: AnalysisInput) -> StrategyDraft:
    return reconcile(analyst.draft(inp), inp)


__all__ = ["AnalysisError", "AnalysisInput", "Analyst", "MockAnalyst", "StrategyDraft", "StrategyItem", "analyze", "get_analyst"]
