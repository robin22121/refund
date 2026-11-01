# 부동산 세금 환급 진단

양도세·취득세·종부세를 낸 사람이 간편인증 한 번으로 홈택스·위택스·등기·대장·초본 자료를 모으고,
환급 가능성을 진단받은 뒤 제휴 세무사를 통해 경정청구하는 서비스입니다.

## 흐름

1. 사용자가 카카오 등 간편인증을 승인합니다 (`POST /auth/start`).
2. 수집기가 홈택스 신고내역, 위택스 납부내역, 등기부, 건축물대장, 토지이용계획, 주민등록초본을 모읍니다.
3. 룰 엔진이 환급 사유를 찾고, 경정청구 기한이 남은 것만 돌려줍니다 (`POST /diagnosis/{session_id}`).
4. (예정) AI가 전략 초안을 쓰고 세무사가 검토해 경정청구를 대행합니다.

## 구조

```
backend/app/
  models.py          납세자 자료와 진단 결과 모델
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
