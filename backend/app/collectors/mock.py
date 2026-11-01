"""하이픈 계약 전 개발용 가짜 수집기. 룰마다 하나씩 걸리도록 만든 예시 자료다."""
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
    def start_auth(self, req: EasyAuthRequest) -> str:
        return f"mock-{req.phone}"

    def collect(self, session_id: str) -> TaxpayerData:
        return sample_taxpayer()


def sample_taxpayer() -> TaxpayerData:
    return TaxpayerData(
        name="홍길동",
        birth_date=date(1965, 3, 2),
        properties=[
            Property(
                id="apt-1",
                kind=PropertyKind.HOUSE,
                address="서울특별시 마포구 예시로 1, 101동 1001호",
                acquired_on=date(2012, 6, 1),
                registered_price=480_000_000,
                disposed_on=date(2023, 9, 15),
            ),
            Property(
                id="apt-2",
                kind=PropertyKind.HOUSE,
                address="경기도 성남시 분당구 예시로 2",
                acquired_on=date(2023, 10, 1),
            ),
            Property(
                id="land-1",
                kind=PropertyKind.LAND,
                address="충청남도 예산군 예시리 100",
                pnu="4481025021101000000",
                land_category="답",
                zoning="농림지역",
                acquired_on=date(2005, 4, 1),
                disposed_on=date(2022, 11, 30),
            ),
            Property(
                id="bldg-1",
                kind=PropertyKind.BUILDING,
                address="대전광역시 유성구 예시로 3",
                acquired_on=date(2020, 2, 1),
                acquisition_cause=AcquisitionCause.NEW_BUILD,
                building_approved_on=date(2020, 2, 1),
                disposed_on=date(2024, 5, 20),
            ),
        ],
        residences=[
            Residence(address="서울특별시 마포구 예시로 1", moved_in=date(2012, 7, 1), moved_out=date(2023, 9, 20), property_id="apt-1"),
            Residence(address="경기도 성남시 분당구 예시로 2", moved_in=date(2023, 9, 20)),
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
