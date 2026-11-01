# 부동산 세금 환급 진단

양도세·취득세·종부세를 낸 사람이 간편인증 한 번으로 홈택스·위택스·등기·대장·초본 자료를 모으고,
환급 가능성을 진단받은 뒤 제휴 세무사를 통해 경정청구하는 서비스입니다.

## 흐름

1. 고객이 양도한 부동산 소재지와 "가장 세금이 아까운 부분"을 적습니다 (`POST /cases`). 소재지로 등기부·대장·토지이용계획을 자동 조회합니다.
2. 제휴 세무사 앞으로 전자위임장에 서명합니다 (`POST /cases/{id}/mandate`).
3. 서명이 끝난 건만 홈택스 간편인증을 시작합니다 (`POST /cases/{id}/auth/start`).
4. 사용자가 휴대폰에서 승인하면 자료를 모아 룰 엔진으로 진단합니다 (`POST /cases/{id}/diagnosis`). 경정청구 기한이 남은 것만 돌려줍니다.
5. (예정) AI가 검토 희망 항목부터 전략 초안을 쓰고 세무사가 검토해 경정청구를 대행합니다.

수익: 진단은 무료, 환급 성공보수는 세무사가 받고 플랫폼은 세무사에게 건당 고정 이용료를 받습니다.

## 구조

```
backend/app/
  models.py          납세자 자료와 진단 결과 모델
  cases.py           접수 → 위임장 → 인증 → 진단 상태 흐름
  registry.py        소재지로 등기부·대장 조회 (지금은 가짜 데이터)
  collectors/        자료 수집기 (mock: 가짜 데이터, hyphen: 하이픈 API)
  rules/             환급 사유 룰. config.py에 세법 기준값
  main.py            FastAPI 엔드포인트
```

## 실행

```bash
cd backend
python -m venv ../.venv && . ../.venv/bin/activate
pip install -r requirements.txt uvicorn
python -m pytest -q
uvicorn app.main:app --reload   # http://localhost:8000/docs
```

기본 수집기는 가짜 데이터입니다. 하이픈 계약 후 `.env.example`을 참고해 `COLLECTOR=hyphen`과 키를 설정합니다.
