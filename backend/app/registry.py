"""소재지로 등기부·건축물대장·토지대장·토지이용계획을 조회한다.

실제 구현: 하이픈 인터넷등기소 상품(등기부) + 공공데이터 오픈 API(대장, 토지이용계획).
등기부에서 지번(PNU)을 얻어 대장과 토지이용계획을 이어 조회한다.
"""
from typing import Protocol

from .cases import AssetLookup
from .collectors.mock import sample_taxpayer


class RegistryFetcher(Protocol):
    def lookup(self, address: str, applicant_name: str) -> AssetLookup: ...


class MockRegistryFetcher:
    """예시 자료에서 주소 앞부분이 맞는 부동산을 찾는다."""

    def lookup(self, address: str, applicant_name: str) -> AssetLookup:
        key = address.replace(" ", "")
        for p in sample_taxpayer().properties:
            if p.address.replace(" ", "").startswith(key) or key.startswith(p.address.replace(" ", "")):
                return AssetLookup(address=address, property=p, owner_name_matches=True)
        return AssetLookup(address=address, error="등기부를 찾지 못했습니다. 주소를 다시 확인해 주세요.")
