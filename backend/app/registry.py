"""소재지로 등기부·건축물대장·토지대장·토지이용계획을 조회한다.

실제 구현: 하이픈 인터넷등기소 상품(등기부) + 공공데이터 오픈 API(대장, 토지이용계획).
등기부에서 지번(PNU)을 얻어 대장과 토지이용계획을 이어 조회한다.
"""
from typing import Protocol

from .cases import AssetLookup
from .collectors.mock import sample_taxpayer
from .models import Property, PropertyKind


class RegistryFetcher(Protocol):
    def lookup(self, address: str, applicant_name: str) -> AssetLookup: ...


def documents_for(prop: Property) -> list[str]:
    """부동산 종류별로 자동 발급하는 서류."""
    docs = ["등기사항전부증명서"]
    if prop.kind == PropertyKind.LAND:
        docs += ["토지대장", "토지이용계획확인원"]
    else:
        docs += ["건축물대장", "토지대장"]
    return docs


def _norm(s: str) -> str:
    return s.split("(")[0].replace(" ", "")


class MockRegistryFetcher:
    """예시 자료에서 주소 앞부분이 맞는 부동산을 찾는다."""

    def lookup(self, address: str, applicant_name: str) -> AssetLookup:
        key = _norm(address)
        for p in sample_taxpayer().properties:
            addr = _norm(p.address)
            if key and (addr.startswith(key) or key.startswith(addr)):
                return AssetLookup(
                    address=address, property=p, owner_name_matches=True, documents=documents_for(p)
                )
        return AssetLookup(address=address, error="등기부를 찾지 못했습니다. 주소를 다시 확인해 주세요.")


def search_addresses(query: str, limit: int = 10) -> list[dict]:
    """주소 검색. 실제로는 행정안전부 도로명주소 API(juso.go.kr)로 바꾼다.

    지금은 예시 자료의 부동산 주소만 돌려준다. 띄어쓴 낱말이 모두 들어 있으면 맞는 것으로 본다.
    """
    words = query.split()
    if not words:
        return []
    results = []
    for p in sample_taxpayer().properties:
        if all(w in p.address for w in words):
            results.append({"address": p.address, "kind": p.kind.value})
    return results[:limit]
