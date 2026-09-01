"""보조사업 공시 크롤링 공통 모듈 (Vercel Python Functions).

설계 원칙
  · 사이트의 내부 selector에 의존하지 않는다. 페이지의 모든 <table>을 훑어
    헤더가 보조금 공시 항목과 가장 많이 겹치는 표를 자동 선택한다.
  · 요청 파라미터는 브라우저 DevTools의 "Copy as cURL"을 그대로 재생한다.
    (.do 엔드포인트는 세션·CSRF 토큰을 쓰는 경우가 많아 이 방식이 가장 견고)
  · 공개 데이터만, 정중하게: robots.txt 확인 + 요청 간 지연 + 대상 호스트 제한.
"""
from __future__ import annotations

import json
import os
import re
import shlex
import time
import urllib.parse
import urllib.robotparser
from html import unescape

import requests
from bs4 import BeautifulSoup

UA = os.environ.get(
    "CRAWL_UA",
    "SubsidyAuditBot/1.0 (public disclosure analysis; contact via deployer)",
)
# SSRF·오남용 방지: 정부 공개 포털만 허용 (환경변수로 조정)
ALLOWED_SUFFIXES = tuple(
    s.strip() for s in os.environ.get("ALLOWED_HOSTS", ".go.kr,.or.kr").split(",") if s.strip()
)
DEFAULT_DELAY_MS = int(os.environ.get("CRAWL_DELAY_MS", "800"))

# 공시 표에서 인식할 컬럼 별칭 (프런트엔드 CSV 임포터와 동일 사전)
ALIAS = {
    "project":     ["보조사업명", "사업명", "세부사업명", "내역사업명", "사업", "과제명"],
    "grantor":     ["교부기관", "소관기관", "중앙관서", "교부처", "소관부처", "지자체"],
    "recipient":   ["보조사업자", "보조사업자명", "수급자", "수행기관", "단체명", "업체명", "기관명"],
    "bizno":       ["사업자등록번호", "보조사업자사업자번호", "사업자번호", "고유번호"],
    "ceo":         ["대표자", "대표자명", "대표"],
    "item":        ["비목", "비목명", "예산비목", "계정과목", "지출항목", "세목"],
    "vendor":      ["거래처", "거래처명", "지급처", "수취인", "계약상대자", "업체", "공급자"],
    "vendorBizno": ["거래처사업자번호", "공급자사업자번호", "계약상대자사업자번호"],
    "method":      ["계약방법", "계약방식", "구매방법", "계약구분"],
    "amount":      ["집행액", "지출액", "금액", "계약금액", "지급액", "집행금액", "결제금액", "지출금액"],
    "grant":       ["교부액", "교부금액", "보조금액", "지원금액", "예산액", "총사업비"],
    "date":        ["집행일자", "지출일자", "거래일자", "계약일자", "지급일", "일자", "결제일"],
    "doc":         ["증빙", "증빙유형", "증빙서류", "세금계산서번호", "증빙번호"],
    "note":        ["비고", "적요", "내용", "산출내역", "성명"],
}
_NORM = re.compile(r"[\s\"'()\[\]·:]")


def norm(s) -> str:
    return _NORM.sub("", str(s or "")).lower()


def to_num(v) -> float:
    m = re.sub(r"[^0-9.\-]", "", str(v or ""))
    try:
        return float(m) if m not in ("", "-", ".") else 0.0
    except ValueError:
        return 0.0


class CrawlError(Exception):
    pass


def check_host(url: str) -> str:
    """대상 호스트가 허용 목록에 있는지 확인. 이 API를 열린 프록시로 만들지 않기 위함."""
    p = urllib.parse.urlparse(url)
    if p.scheme not in ("http", "https"):
        raise CrawlError(f"지원하지 않는 스킴입니다: {p.scheme}")
    host = (p.hostname or "").lower()
    if not host:
        raise CrawlError("URL에 호스트가 없습니다.")
    if not any(host.endswith(sfx) for sfx in ALLOWED_SUFFIXES):
        raise CrawlError(
            f"허용되지 않은 호스트입니다: {host} "
            f"(허용: {', '.join(ALLOWED_SUFFIXES)} — 환경변수 ALLOWED_HOSTS로 변경)"
        )
    return host


_robots_cache: dict[str, urllib.robotparser.RobotFileParser | None] = {}


def robots_allows(url: str) -> tuple[bool, str]:
    """robots.txt 확인. 가져오지 못하면 허용으로 간주하되 사유를 남긴다."""
    p = urllib.parse.urlparse(url)
    root = f"{p.scheme}://{p.netloc}"
    if root not in _robots_cache:
        rp = urllib.robotparser.RobotFileParser()
        rp.set_url(root + "/robots.txt")
        try:
            r = requests.get(root + "/robots.txt", timeout=6, headers={"User-Agent": UA})
            if r.status_code == 200:
                rp.parse(r.text.splitlines())
                _robots_cache[root] = rp
            else:
                _robots_cache[root] = None
        except requests.RequestException:
            _robots_cache[root] = None
    rp = _robots_cache[root]
    if rp is None:
        return True, "robots.txt 없음/조회 실패 — 허용으로 간주"
    ok = rp.can_fetch(UA, url)
    return ok, "robots.txt 허용" if ok else "robots.txt가 이 경로의 수집을 금지합니다"


# ────────────────────────── cURL 파싱 ──────────────────────────
def parse_curl(curl: str) -> dict:
    """DevTools의 'Copy as cURL'을 요청 명세로 변환."""
    curl = curl.strip()
    if curl.startswith("curl"):
        curl = curl[4:]
    # 줄바꿈 이어쓰기(\ 또는 ^) 정리
    curl = re.sub(r"[\\^]\s*\n", " ", curl).replace("\n", " ")
    try:
        parts = shlex.split(curl)
    except ValueError as e:
        raise CrawlError(f"cURL 구문을 해석하지 못했습니다: {e}")

    spec = {"url": "", "method": "", "headers": {}, "data": None, "cookies": {}}
    i = 0
    while i < len(parts):
        a = parts[i]
        if a in ("-X", "--request"):
            i += 1; spec["method"] = parts[i].upper()
        elif a in ("-H", "--header"):
            i += 1
            k, _, v = parts[i].partition(":")
            k, v = k.strip(), v.strip()
            if k.lower() == "cookie":
                for c in v.split(";"):
                    ck, _, cv = c.strip().partition("=")
                    if ck:
                        spec["cookies"][ck] = cv
            elif k.lower() not in ("content-length", "host", "accept-encoding"):
                spec["headers"][k] = v
        elif a in ("-b", "--cookie"):
            i += 1
            for c in parts[i].split(";"):
                ck, _, cv = c.strip().partition("=")
                if ck:
                    spec["cookies"][ck] = cv
        elif a in ("-d", "--data", "--data-raw", "--data-urlencode", "--data-binary"):
            i += 1
            spec["data"] = (spec["data"] + "&" + parts[i]) if spec["data"] else parts[i]
        elif a in ("--compressed", "-s", "--silent", "-L", "--location", "-i", "-k", "--insecure"):
            pass
        elif not a.startswith("-"):
            spec["url"] = a
        i += 1

    if not spec["url"]:
        raise CrawlError("cURL에서 URL을 찾지 못했습니다.")
    if not spec["method"]:
        spec["method"] = "POST" if spec["data"] else "GET"
    spec["headers"].setdefault("User-Agent", UA)
    return spec


def body_to_dict(data: str | None) -> dict | None:
    """폼 인코딩 바디를 dict로. JSON 바디면 None을 돌려주고 원문을 쓰게 한다."""
    if not data:
        return None
    t = data.strip()
    if t.startswith("{") or t.startswith("["):
        return None
    out: dict[str, str] = {}
    for kv in t.split("&"):
        if not kv:
            continue
        k, _, v = kv.partition("=")
        out[urllib.parse.unquote_plus(k)] = urllib.parse.unquote_plus(v)
    return out or None


# ────────────────────────── 표 추출 ──────────────────────────
def _cells(tr) -> list[str]:
    return [unescape(td.get_text(" ", strip=True)) for td in tr.find_all(["td", "th"])]


def score_header(cells: list[str]) -> tuple[int, dict]:
    """헤더 행이 공시 항목과 얼마나 겹치는지 점수화하고 컬럼 인덱스 맵을 만든다."""
    m: dict[str, int] = {}
    for key, names in ALIAS.items():
        idx = next((i for i, c in enumerate(cells) if norm(c) in {norm(n) for n in names}), -1)
        if idx < 0:
            idx = next((i for i, c in enumerate(cells)
                        if any(norm(n) and norm(n) in norm(c) for n in names)), -1)
        if idx >= 0:
            m[key] = idx
    return len(m), m


def extract_tables(html: str) -> list[dict]:
    """페이지의 모든 표를 훑어 (점수, 컬럼맵, 행들)로 반환. 점수 높은 순."""
    soup = BeautifulSoup(html, "lxml")
    found = []
    for t_i, table in enumerate(soup.find_all("table")):
        rows = [_cells(tr) for tr in table.find_all("tr")]
        rows = [r for r in rows if any(c.strip() for c in r)]
        if len(rows) < 2:
            continue
        best_i, best_n, best_m = 0, -1, {}
        for i, r in enumerate(rows[:3]):
            n, m = score_header(r)
            if n > best_n:
                best_i, best_n, best_m = i, n, m
        found.append({
            "index": t_i, "score": best_n, "map": best_m,
            "header": rows[best_i], "rows": rows[best_i + 1:],
            "caption": (table.find("caption").get_text(strip=True) if table.find("caption") else ""),
        })
    found.sort(key=lambda x: (-x["score"], -len(x["rows"])))
    return found


def rows_from_table(tbl: dict, extra: dict | None = None) -> list[dict]:
    m = tbl["map"]
    out = []
    for r in tbl["rows"]:
        def g(k):
            i = m.get(k)
            return r[i].strip() if i is not None and i < len(r) else ""
        rec = {
            "project": g("project"), "grantor": g("grantor"), "recipient": g("recipient"),
            "bizno": g("bizno"), "ceo": g("ceo"), "item": g("item"),
            "vendor": g("vendor"), "vendorBizno": g("vendorBizno"), "method": g("method"),
            "amount": to_num(g("amount")), "grant": to_num(g("grant")),
            "date": g("date"), "doc": g("doc"), "note": g("note"),
        }
        if extra:
            for k, v in extra.items():
                if not rec.get(k):
                    rec[k] = v
        if rec["project"] or rec["recipient"] or rec["amount"]:
            out.append(rec)
    return out


def rows_with_ids(html: str, id_regex: str, id_from: str = "onclick") -> list[tuple[dict, str]]:
    """목록 표의 각 행을 파싱하면서, 그 행이 가리키는 상세 ID를 함께 뽑는다.

    상세 페이지에는 사업명·보조사업자가 없는 경우가 많으므로, 목록 행의 정보를
    상세 집행내역에 승계시켜야 사업별로 분리 분석할 수 있다.
    """
    soup = BeautifulSoup(html, "lxml")
    rx = re.compile(id_regex)
    best_tbl, best_score = None, -1
    for table in soup.find_all("table"):
        trs = [tr for tr in table.find_all("tr") if _cells(tr)]
        if len(trs) < 2:
            continue
        hi, n, m = 0, -1, {}
        for i, tr in enumerate(trs[:3]):
            sc, mm = score_header(_cells(tr))
            if sc > n:
                hi, n, m = i, sc, mm
        if n > best_score:
            best_tbl, best_score = (table, trs, hi, m), n
    if not best_tbl or best_score < 2:
        return []
    _table, trs, hi, m = best_tbl

    out = []
    for tr in trs[hi + 1:]:
        cells = _cells(tr)
        if not any(c.strip() for c in cells):
            continue
        # 행 자체 또는 내부 a/button의 onclick·href에서 ID 탐색
        src = " ".join(filter(None, [
            tr.get("onclick") or "",
            *[el.get("onclick") or "" for el in tr.find_all(["a", "button", "td"])],
            *([el.get("href") or "" for el in tr.find_all("a")] if id_from != "onclick" else []),
            *[el.get("href") or "" for el in tr.find_all("a")],
        ]))
        found = rx.findall(src)
        pid = found[0] if found else ""
        rec = rows_from_table({"map": m, "rows": [cells]})
        if rec and pid:
            out.append((rec[0], pid))
    return out


def rows_from_json(payload) -> list[dict]:
    """JSON 응답에서 레코드 배열을 찾아 표준 스키마로 변환."""
    def find_list(o, depth=0):
        if depth > 6:
            return None
        if isinstance(o, list) and o and isinstance(o[0], dict):
            return o
        if isinstance(o, dict):
            for v in o.values():
                r = find_list(v, depth + 1)
                if r:
                    return r
        return None

    items = find_list(payload)
    if not items:
        return []
    keys = list(items[0].keys())
    _, m = score_header(keys)
    out = []
    for it in items:
        def g(k):
            i = m.get(k)
            return str(it.get(keys[i], "")).strip() if i is not None else ""
        rec = {
            "project": g("project"), "grantor": g("grantor"), "recipient": g("recipient"),
            "bizno": g("bizno"), "ceo": g("ceo"), "item": g("item"),
            "vendor": g("vendor"), "vendorBizno": g("vendorBizno"), "method": g("method"),
            "amount": to_num(g("amount")), "grant": to_num(g("grant")),
            "date": g("date"), "doc": g("doc"), "note": g("note"),
        }
        if rec["project"] or rec["recipient"] or rec["amount"]:
            out.append(rec)
    return out


# ────────────────────────── 요청 실행 ──────────────────────────
def make_session(spec: dict) -> requests.Session:
    s = requests.Session()
    s.headers.update(spec.get("headers") or {"User-Agent": UA})
    for k, v in (spec.get("cookies") or {}).items():
        s.cookies.set(k, v)
    return s


def set_page(spec: dict, page_param: str, page: int) -> dict:
    """요청 명세의 페이지 파라미터만 교체한 사본을 만든다."""
    out = json.loads(json.dumps(spec))
    if out.get("data"):
        d = body_to_dict(out["data"])
        if d is not None:
            d[page_param] = str(page)
            out["data"] = urllib.parse.urlencode(d)
        else:  # JSON 바디
            try:
                j = json.loads(out["data"])
                j[page_param] = page
                out["data"] = json.dumps(j)
            except (ValueError, TypeError):
                pass
    else:
        u = urllib.parse.urlparse(out["url"])
        q = dict(urllib.parse.parse_qsl(u.query))
        q[page_param] = str(page)
        out["url"] = urllib.parse.urlunparse(u._replace(query=urllib.parse.urlencode(q)))
    return out


def fetch(session: requests.Session, spec: dict, timeout: int = 20) -> requests.Response:
    check_host(spec["url"])
    kw = {"timeout": timeout, "allow_redirects": True}
    if spec.get("data"):
        d = body_to_dict(spec["data"])
        if d is not None:
            kw["data"] = d
        else:
            kw["data"] = spec["data"].encode("utf-8")
    r = session.request(spec.get("method", "GET"), spec["url"], **kw)
    if not r.encoding or r.encoding.lower() == "iso-8859-1":
        r.encoding = r.apparent_encoding or "utf-8"
    return r


def parse_response(r: requests.Response, extra: dict | None = None) -> tuple[list[dict], dict]:
    """응답에서 행을 추출. (행, 진단정보)"""
    ctype = (r.headers.get("content-type") or "").lower()
    text = r.text
    if "json" in ctype or text.lstrip()[:1] in ("{", "["):
        try:
            rows = rows_from_json(json.loads(text))
            return rows, {"kind": "json", "bytes": len(text), "rows": len(rows)}
        except ValueError:
            pass
    tables = extract_tables(text)
    if not tables:
        return [], {"kind": "html", "bytes": len(text), "tables": 0, "rows": 0}
    best = tables[0]
    rows = rows_from_table(best, extra) if best["score"] >= 2 else []
    return rows, {
        "kind": "html", "bytes": len(text), "tables": len(tables),
        "chosenTableScore": best["score"], "chosenHeader": best["header"][:12],
        "rows": len(rows),
    }
