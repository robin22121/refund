import os

from .base import Collector, EasyAuthRequest
from .mock import MockCollector


def get_collector() -> Collector:
    """환경변수 COLLECTOR로 수집기를 고른다. 기본값은 가짜 데이터."""
    if os.getenv("COLLECTOR", "mock") == "hyphen":
        from .hyphen import HyphenCollector

        return HyphenCollector.from_env()
    return MockCollector()


__all__ = ["Collector", "EasyAuthRequest", "MockCollector", "get_collector"]
