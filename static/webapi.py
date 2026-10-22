"""정적 사이트용: 브라우저(Pyodide) 안에서 server.py 의 /api/* 요청을 그대로 처리 (worker.js 가 부름)"""
import json
from urllib.parse import parse_qs, urlparse

import autorecipe

autorecipe.OFFLINE = True  # 브라우저에선 코덱스에 못 물어봄(CORS) → 미리 받아 둔 캐시만 씀

import api  # noqa: E402  (OFFLINE 을 먼저 켜고 데이터 불러옴)


def handle(method, path, body_text):
    """반환: (HTTP 상태, JSON 문자열)"""
    u = urlparse(path)
    body = json.loads(body_text) if body_text else None
    try:
        if u.path == "/api/meta":
            out = api.meta()
        elif u.path == "/api/items":
            out = api.items_api(parse_qs(u.query).get("q", [""])[0])
        elif u.path == "/api/plan":
            out = api.plan_api(body)
        elif u.path == "/api/manual_recipes":
            out = api.manual_list() if method == "GET" else api.manual_save(body)
        elif u.path.startswith("/api/manual_recipes/") and method == "DELETE":
            out = api.manual_delete(int(u.path.rsplit("/", 1)[1]))
        else:
            return 404, json.dumps({"error": f"없는 요청: {method} {path}"}, ensure_ascii=False)
    except ValueError as e:
        return 400, json.dumps({"error": str(e)}, ensure_ascii=False)
    return 200, json.dumps(out, ensure_ascii=False)


def manual_text():
    """직접 추가한 조합식 파일 내용 (브라우저 저장소에 보관해 다음에 다시 넣음)"""
    return api.plan.MANUAL.read_text(encoding="utf-8")
