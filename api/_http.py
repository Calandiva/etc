"""Vercel Python Functions 공통 HTTP 헬퍼."""
import json
from http.server import BaseHTTPRequestHandler

CORS = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Methods": "POST, GET, OPTIONS",
    "Access-Control-Allow-Headers": "Content-Type",
}


class JsonHandler(BaseHTTPRequestHandler):
    """do_POST에서 handle(payload)를 구현하면 JSON 입출력이 처리된다."""

    def _send(self, code: int, obj):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        for k, v in CORS.items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(204)
        for k, v in CORS.items():
            self.send_header(k, v)
        self.end_headers()

    def do_GET(self):
        self._send(200, {"ok": True, "usage": getattr(self, "USAGE", "POST JSON을 보내세요.")})

    def do_POST(self):
        try:
            n = int(self.headers.get("content-length") or 0)
            raw = self.rfile.read(n).decode("utf-8") if n else "{}"
            payload = json.loads(raw or "{}")
        except (ValueError, UnicodeDecodeError) as e:
            return self._send(400, {"ok": False, "error": f"JSON 본문을 읽지 못했습니다: {e}"})
        try:
            self._send(200, {"ok": True, **self.handle_payload(payload)})
        except Exception as e:  # 크롤링 실패는 사용자에게 이유를 그대로 전달
            self._send(400, {"ok": False, "error": f"{type(e).__name__}: {e}"})

    def log_message(self, *args):  # Vercel 로그 소음 억제
        pass
