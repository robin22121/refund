"""하이픈 계약 전 개발용 가짜 수집기. 룰마다 하나씩 걸리도록 만든 예시 자료다."""
import uuid
from datetime import date

from ..models import (
    AcquisitionCause,
    CapitalGainsReturn,
    LocalTaxPayment,
    Property,
    PropertyKind,
    Residence,
    TaxpayerData,
)
from .base import EasyAuthRequest


class MockCollector:
    """하이픈 간편인증이 된 것처럼 동작한다. 자료는 예시 자료에 신청인 이름만 바꿔 돌려준다."""

    def __init__(self) -> None:
        self._sessions: dict[str, EasyAuthRequest] = {}

    def start_auth(self, req: EasyAuthRequest) -> str:
        session_id = f"mock-{uuid.uuid4().hex[:12]}"
        self._sessions[session_id] = req
        return session_id

    def collect(self, session_id: str) -> TaxpayerData:
        data = sample_taxpayer()
        req = self._sessions.get(session_id)
        if req is not None:
            data.name, data.birth_date = req.name, req.birth_date
        return data


def sample_taxpayer() -> TaxpayerData:
    return TaxpayerData(
        name="홍길동",
        birth_date=date(1965, 3, 2),
        properties=[
            Property(
                id="apt-1",
                kind=PropertyKind.HOUSE,
                address="서울특별시 마포구 성미산로 77, 101동 1001호 (성산동, 성미산한빛아파트)",
                acquired_on=date(2012, 6, 1),
                registered_price=480_000_000,
                disposed_on=date(2023, 9, 15),
            ),
            Property(
                id="apt-2",
                kind=PropertyKind.HOUSE,
                address="경기도 성남시 분당구 정자일로 95, 203동 704호 (정자동, 느티숲마을아파트)",
                acquired_on=date(2023, 10, 1),
            ),
            Property(
                id="land-1",
                kind=PropertyKind.LAND,
                address="충청남도 예산군 덕산면 사동리 312",
                pnu="4481025021101000000",
                land_category="답",
                zoning="농림지역",
                acquired_on=date(2005, 4, 1),
                disposed_on=date(2022, 11, 30),
            ),
            Property(
                id="bldg-1",
                kind=PropertyKind.BUILDING,
                address="대전광역시 유성구 대학로155번길 12",
                acquired_on=date(2020, 2, 1),
                acquisition_cause=AcquisitionCause.NEW_BUILD,
                building_approved_on=date(2020, 2, 1),
                disposed_on=date(2024, 5, 20),
            ),
        ],
        residences=[
            Residence(address="서울특별시 마포구 성미산로 77, 101동 1001호", moved_in=date(2012, 7, 1), moved_out=date(2023, 9, 20), property_id="apt-1"),
            Residence(address="경기도 성남시 분당구 정자일로 95, 203동 704호", moved_in=date(2023, 9, 20)),
        ],
        capital_gains_returns=[
            CapitalGainsReturn(
                property_id="apt-1",
                transfer_date=date(2023, 9, 15),
                transfer_price=950_000_000,
                acquisition_price=480_000_000,
                necessary_expenses=3_000_000,
                long_term_deduction_rate=0.22,
                exemption_applied=False,
                tax_paid=78_000_000,
            ),
            CapitalGainsReturn(
                property_id="land-1",
                transfer_date=date(2022, 11, 30),
                transfer_price=300_000_000,
                acquisition_price=60_000_000,
                non_business_land_surcharge=True,
                tax_paid=70_000_000,
            ),
            CapitalGainsReturn(
                property_id="bldg-1",
                transfer_date=date(2024, 5, 20),
                transfer_price=1_100_000_000,
                acquisition_price=700_000_000,
                used_converted_price=True,
                tax_paid=60_000_000,
            ),
        ],
        local_tax_payments=[
            LocalTaxPayment(tax_type="취득세", property_id="apt-1", amount=5_280_000, paid_on=date(2012, 6, 20)),
        ],
    )
