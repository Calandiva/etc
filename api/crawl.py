"""POST /api/crawl — 보조사업 공시 목록/상세를 페이지 단위로 수집.

body: {
  "curl": "<목록 요청 cURL>",        # 필수(또는 url/method/data)
  "pageParam": "pageIndex",          # 페이지 파라미터명
  "startPage": 1, "pages": 10,       # 수집 범위
  "delayMs": 800,                    # 요청 간 지연(정중한 수집)
  "detail": {                        # 선택: 개별 사업 상세까지 수집
     "urlTemplate": "https://www.bojo.go.kr/ea/getEA001202View.do?bsnsId={id}",
     "idFrom": "onclick", "idRegex": "'([0-9A-Za-z_-]{4,40})'", "max": 30
  },
  "budgetMs": 50000                  # 이 호출의 시간 예산(초과 시 커서 반환)
}
응답: { rows, pagesDone, nextPage, diagnostics[] } — rows는 분석 엔진 입력 스키마
"""
import re
import time

from _bojo import (CrawlError, check_host, extract_tables, fetch, make_session,
                   parse_curl, parse_response, robots_allows, rows_from_table,
                   rows_with_ids, set_page, DEFAULT_DELAY_MS)
from _http import JsonHandler
from bs4 import BeautifulSoup

MAX_PAGES = 200
MAX_ROWS = 20000


class handler(JsonHandler):
    USAGE = 'POST {"curl":"...","pageParam":"pageIndex","pages":10}'

    def handle_payload(self, p):
        spec = parse_curl(p["curl"]) if p.get("curl") else {
            "url": p.get("url", ""), "method": (p.get("method") or "GET").upper(),
            "headers": p.get("headers") or {}, "data": p.get("data"), "cookies": p.get("cookies") or {},
        }
        if not spec["url"]:
            raise CrawlError("curl 또는 url 중 하나는 필요합니다.")
        check_host(spec["url"])
        allowed, robots_note = robots_allows(spec["url"])
        if not allowed:
            raise CrawlError(f"수집이 허용되지 않습니다 — {robots_note}")

        page_param = p.get("pageParam") or "pageIndex"
        start = max(1, int(p.get("startPage") or 1))
        pages = min(MAX_PAGES, max(1, int(p.get("pages") or 5)))
        delay = max(200, int(p.get("delayMs") or DEFAULT_DELAY_MS)) / 1000.0
        budget = max(5000, int(p.get("budgetMs") or 50000)) / 1000.0
        t0 = time.monotonic()

        session = make_session(spec)
        rows, diags, seen = [], [], set()
        page = start
        next_page = None

        for n in range(pages):
            if time.monotonic() - t0 > budget:
                next_page = page
                diags.append({"page": page, "note": "시간 예산 초과 — nextPage부터 이어서 호출하세요"})
                break
            r = fetch(session, set_page(spec, page_param, page))
            got, d = parse_response(r)
            d["page"] = page
            d["httpStatus"] = r.status_code
            diags.append(d)
            if not got:
                diags.append({"page": page, "note": "행 없음 — 마지막 페이지로 판단하고 중단"})
                break
            fresh = []
            for row in got:                       # 같은 페이지가 반복 반환되는 경우 방지
                key = (row["project"], row["recipient"], row["vendor"], row["amount"], row["date"])
                if key in seen:
                    continue
                seen.add(key); fresh.append(row)
            if not fresh:
                diags.append({"page": page, "note": "새 행 없음(동일 페이지 반복) — 중단"})
                break
            rows.extend(fresh)
            if len(rows) >= MAX_ROWS:
                next_page = page + 1
                diags.append({"note": f"행 상한 {MAX_ROWS} 도달 — 중단"})
                break
            page += 1
            if n < pages - 1:
                time.sleep(delay)
        else:
            next_page = page

        detail_rows = []
        det = p.get("detail")
        if det and det.get("urlTemplate") and time.monotonic() - t0 < budget:
            detail_rows, ddiag = self._crawl_details(
                session, spec, det, t0, budget, delay, pages, page_param, start)
            diags.extend(ddiag)
            rows.extend(detail_rows)

        return {
            "rows": rows, "rowCount": len(rows), "detailRows": len(detail_rows),
            "pagesDone": page - start, "nextPage": next_page,
            "robots": robots_note, "elapsedMs": int((time.monotonic() - t0) * 1000),
            "diagnostics": diags,
        }

    def _crawl_details(self, session, spec, det, t0, budget, delay, pages, page_param, start):
        """목록 각 행의 사업 정보를 상세 집행내역에 승계시켜 사업별로 분리 수집."""
        diags = []
        pairs, seen = [], set()
        rx = det.get("idRegex") or r"['\"]([0-9A-Za-z_-]{4,40})['\"]"
        for pg in range(start, start + max(1, pages)):   # 수집한 모든 목록 페이지에서 ID 확보
            if time.monotonic() - t0 > budget:
                break
            r = fetch(session, set_page(spec, page_param, pg))
            got = rows_with_ids(r.text, rx, det.get("idFrom", "onclick"))
            if not got:
                break
            for rec, pid in got:
                if pid not in seen:
                    seen.add(pid); pairs.append((rec, pid))
            time.sleep(delay)
        pairs = pairs[: int(det.get("max") or 30)]
        diags.append({"note": f"상세 수집 대상 {len(pairs)}건",
                      "sample": [{"id": p, "project": r.get("project")} for r, p in pairs[:3]]})

        out = []
        for i, (listrec, pid) in enumerate(pairs):
            if time.monotonic() - t0 > budget:
                diags.append({"note": f"상세 수집 중 시간 예산 초과 ({i}/{len(pairs)})"})
                break
            url = det["urlTemplate"].replace("{id}", pid)
            try:
                check_host(url)
                dr = fetch(session, {"url": url, "method": "GET",
                                     "headers": spec.get("headers"), "cookies": {}})
            except Exception as e:
                diags.append({"id": pid, "error": str(e)})
                continue
            # 목록 행 → 상세 행으로 승계할 사업 식별 정보
            inherit = {k: v for k, v in {
                "project": listrec.get("project"), "recipient": listrec.get("recipient"),
                "grantor": listrec.get("grantor"), "bizno": listrec.get("bizno"),
                "ceo": listrec.get("ceo"), "grant": listrec.get("grant"),
                "note": f"공시ID:{pid}",
            }.items() if v}
            # 상세 페이지에도 개요 표가 있으면 사업자번호·대표자를 보강
            tables = extract_tables(dr.text)
            for t in tables:
                for key in ("bizno", "ceo", "recipient", "project"):
                    if not inherit.get(key) and key in t["map"]:
                        cand = rows_from_table(t)
                        if cand and cand[0].get(key):
                            inherit[key] = cand[0][key]
            if not inherit.get("project"):
                h = BeautifulSoup(dr.text, "lxml").find(["h1", "h2", "title"])
                if h:
                    inherit["project"] = h.get_text(strip=True)[:60]
            # 집행내역 표(행이 가장 많고 점수가 높은 표)에서 거래 추출
            best = next((t for t in tables if t["score"] >= 3 and len(t["rows"]) >= 3), None) \
                   or (tables[0] if tables else None)
            if best and best["score"] >= 2:
                got = rows_from_table(best, extra=inherit)
                out.extend(got)
                diags.append({"id": pid, "project": inherit.get("project"), "rows": len(got)})
            time.sleep(delay)
        return out, diags
