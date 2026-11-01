import re

from fastapi.testclient import TestClient

SIGNATURE = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUg=="
_CASE_PATH = re.compile(r"^/cases/([0-9a-f]{32})")


class TokenClient(TestClient):
    """고객 API 테스트용. 접수할 때 받은 토큰을 기억했다가 그 건 요청에 X-Case-Token을 붙인다."""

    def __init__(self, app):
        super().__init__(app)
        self.tokens: dict[str, str] = {}

    def request(self, method, url, **kw):
        m = _CASE_PATH.match(str(url))
        if m and m[1] in self.tokens:
            kw["headers"] = {"X-Case-Token": self.tokens[m[1]], **(kw.get("headers") or {})}
        res = super().request(method, url, **kw)
        if method.upper() == "POST" and str(url) == "/cases" and res.status_code == 200:
            body = res.json()
            self.tokens[body["id"]] = body["access_token"]
        return res
