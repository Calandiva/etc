"""POST /api/probe — 공시 페이지 구조 진단.

body: { "curl": "<DevTools에서 복사한 cURL>" }  또는  { "url": "...", "method": "GET" }
응답: 표 목록·인식된 컬럼·페이지 파라미터 후보·robots 상태
사이트 요청 규격을 모르는 상태에서 무엇을 보내야 하는지 알아내는 용도.
"""
import re
import urllib.parse

from _bojo import (ALIAS, CrawlError, body_to_dict, check_host, extract_tables,
                   fetch, make_session, parse_curl, robots_allows)
from _http import JsonHandler
from bs4 import BeautifulSoup

PAGE_HINTS = ["pageindex", "pageno", "page", "currentpage", "startpage", "pagenum", "cpage"]


class handler(JsonHandler):
    USAGE = 'POST {"curl":"curl \'https://www.bojo.go.kr/...\' -H ... --data-raw ..."}'

    def handle_payload(self, p):
        spec = parse_curl(p["curl"]) if p.get("curl") else {
            "url": p.get("url", ""), "method": (p.get("method") or "GET").upper(),
            "headers": p.get("headers") or {}, "data": p.get("data"), "cookies": p.get("cookies") or {},
        }
        if not spec["url"]:
            raise CrawlError("curl 또는 url 중 하나는 필요합니다.")
        check_host(spec["url"])
        allowed, robots_note = robots_allows(spec["url"])

        r = fetch(make_session(spec), spec)
        text = r.text
        tables = extract_tables(text)

        # 페이지 파라미터 후보 찾기
        params = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(spec["url"]).query))
        params.update(body_to_dict(spec.get("data")) or {})
        page_candidates = [k for k in params if any(h in k.lower() for h in PAGE_HINTS)]

        # 폼 필드(숨은 토큰 포함) — 어떤 파라미터를 보내야 하는지 파악용
        soup = BeautifulSoup(text, "lxml")
        forms = []
        for f in soup.find_all("form")[:5]:
            forms.append({
                "action": f.get("action", ""), "method": (f.get("method") or "GET").upper(),
                "fields": [{"name": i.get("name"), "type": i.get("type", "text"),
                            "value": (i.get("value") or "")[:40]}
                           for i in f.find_all(["input", "select"])[:30] if i.get("name")],
            })
        # 상세 링크 후보 (개별 보조사업 공시로 들어가는 링크)
        links = []
        for el in soup.find_all(["a", "button", "tr", "td"])[:600]:
            href, onclick = el.get("href") or "", el.get("onclick") or ""
            if not (onclick or ".do" in href or "javascript:" in href.lower()):
                continue
            ids = re.findall(r"['\"]([A-Za-z0-9_\-]{4,40})['\"]", onclick or href)
            links.append({"tag": el.name, "text": el.get_text(" ", strip=True)[:40],
                          "href": href[:120], "onclick": onclick[:160], "idCandidates": ids[:4]})
        seen, uniq = set(), []
        for l in links:
            k = (l["href"], l["onclick"][:60], l["text"][:20])
            if k not in seen:
                seen.add(k); uniq.append(l)

        return {
            "status": r.status_code, "finalUrl": r.url,
            "contentType": r.headers.get("content-type", ""), "bytes": len(text),
            "robots": {"allowed": allowed, "note": robots_note},
            "sentParams": list(params.keys()),
            "pageParamCandidates": page_candidates or ["pageIndex", "pageNo"],
            "tables": [{"index": t["index"], "matchScore": t["score"], "caption": t["caption"],
                        "header": t["header"][:14], "rowCount": len(t["rows"]),
                        "recognizedColumns": list(t["map"].keys()),
                        "sampleRow": (t["rows"][0][:14] if t["rows"] else [])}
                       for t in tables[:6]],
            "forms": forms,
            "detailLinkSamples": uniq[:12],
            "knownAliases": {k: v[:3] for k, v in ALIAS.items()},
            "hint": ("표가 인식되면 /api/crawl 에 같은 curl과 pageParam을 보내 수집하세요. "
                     "표가 0개면 데이터가 XHR로 따로 로드되는 것이므로, "
                     "DevTools Network 탭에서 실제 데이터 요청(XHR)의 cURL을 복사해 다시 진단하세요."),
        }
