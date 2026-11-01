"""룰에서 쓰는 기준값. 세법 개정 시 여기만 고치면 된다.

TODO(이지원): 양도·취득 시점별로 기준이 다르면 기간별 값으로 바꿔 주세요.
"""

# 1세대 1주택 비과세
EXEMPT_MIN_HOLDING_YEARS = 2
EXEMPT_PRICE_LIMIT = 1_200_000_000  # 고가주택 기준 양도가액(원)

# 1세대 1주택 장기보유특별공제 (보유·거주 각 연 4%, 각 최대 40%)
LTHD_HOLDING_RATE_PER_YEAR = 0.04
LTHD_RESIDENCE_RATE_PER_YEAR = 0.04
LTHD_EACH_CAP = 0.40
LTHD_MIN_RESIDENCE_YEARS = 2

# 신축·증축 건물 환산취득가액 가산세 대상 기간(년)
CONVERTED_PRICE_PENALTY_YEARS = 5

# 경정청구 기한: 법정신고기한으로부터(년)
CLAIM_PERIOD_YEARS = 5
