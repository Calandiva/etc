"""보조금통합포털 정보공시 수집기 — GitHub Actions에서 실행.

이 세션(개발 환경)은 .go.kr 접근이 차단되어 있으므로, 실제 수집은 제약 없는
GitHub Actions 러너에서 수행하고 결과를 저장소에 커밋한다. 개발 세션은
recon 결과를 읽어 config를 보정하는 조종석 역할을 한다.

모드
  recon   — 후보 페이지들을 그대로 떠와 구조 리포트 생성 (data/recon/)
            표·폼·XHR 후보·상세링크 패턴을 뽑아 config 보정 근거를 만든다.
  collect — crawler/config.json 명세대로 목록+개별 사업 상세를 수집해
            data/disclosures.json(원본 행) + data/analysis.json(요건 모델 평가) 생성.

정중한 수집: robots.txt 확인, 요청 간 지연, 식별 가능한 UA, 페이지·행 상한.
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
import pathlib
import urllib.parse

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT / "api"))
sys.path.insert(0, str(HERE))

from _bojo import (extract_tables, fetch, make_session, parse_response,        # noqa: E402
                   robots_allows, rows_with_ids, rows_from_table, set_page)
from model import analyze                                                       # noqa: E402
from bs4 import BeautifulSoup                                                   # noqa: E402

DATA = ROOT / "data"
RECON = DATA / "recon"

RECON_TARGETS = [
    # (이름, URL, method, data)
    ("bojo-home",        "https://www.bojo.go.kr/bojo.do", "GET", None),
    ("bojo-ea-guide",    "https://www.bojo.go.kr/ea/getEA001101View.do", "GET", None),
    ("bojo-ea-list",     "https://www.bojo.go.kr/ea/getEA001201View.do", "GET", None),
    ("bojo-ea-list-p1",  "https://www.bojo.go.kr/ea/getEA001201View.do", "POST", "pageIndex=1"),
    ("bojo-opn-biz",     "https://bojo.go.kr/opn/im/im002/getIM002001QView.do", "GET", None),
    ("bojo-opn-exec",    "https://bojo.go.kr/opn/ig/ig002/getIG002002QView.do", "GET", None),
    ("gosims-opn",       "https://opn.gosims.go.kr/opn/iz/iz000/getIZ000002QView.do", "GET", None),
]

XHR_RX = re.compile(
    r"""(?:url\s*[:=]\s*|open\(['"](?:GET|POST)['"]\s*,\s*|action\s*=\s*|fetch\()\s*['"]([^'"]{4,120}\.do[^'"]{0,80})['"]""",
    re.I)


def _spec(url, method="GET", data=None):
    return {"url": url, "method": method, "data": data,
            "headers": {"User-Agent": os.environ.get(
                "CRAWL_UA", "SubsidyDisclosureResearch/1.0 (public data; github actions)"),
                "Accept": "text/html,application/json;q=0.9,*/*;q=0.8"},
            "cookies": {}}


def recon():
    RECON.mkdir(parents=True, exist_ok=True)
    report = []
    for name, url, method, data in RECON_TARGETS:
        entry = {"name": name, "url": url, "method": method, "data": data}
        try:
            ok, note = robots_allows(url)
            entry["robots"] = note
            if not ok:
                entry["skipped"] = "robots 금지"
                report.append(entry)
                continue
            s = make_session(_spec(url))
            r = fetch(s, _spec(url, method, data), timeout=25)
            html = r.text
            entry.update(status=r.status_code, finalUrl=r.url, bytes=len(html),
                         contentType=r.headers.get("content-type", ""))
            (RECON / f"{name}.html").write_text(html[:400_000], encoding="utf-8")
            tables = extract_tables(html)
            entry["tables"] = [{"index": t["index"], "score": t["score"],
                                "header": t["header"][:12], "rows": len(t["rows"]),
                                "caption": t["caption"]} for t in tables[:5]]
            soup = BeautifulSoup(html, "lxml")
            entry["forms"] = [{"action": f.get("action", ""), "method": (f.get("method") or "GET").upper(),
                               "fields": [i.get("name") for i in f.find_all(["input", "select"]) if i.get("name")][:25]}
                              for f in soup.find_all("form")[:4]]
            entry["xhrCandidates"] = sorted({m for m in XHR_RX.findall(html)})[:25]
            onclicks = []
            for el in soup.find_all(["a", "tr", "button"], onclick=True)[:200]:
                oc = el.get("onclick", "")
                if oc and oc not in onclicks:
                    onclicks.append(oc)
            entry["onclickSamples"] = onclicks[:15]
            title = soup.find("title")
            entry["title"] = title.get_text(strip=True) if title else ""
        except Exception as e:  # 러너에서도 막히거나 구조가 다르면 그대로 보고
            entry["error"] = f"{type(e).__name__}: {e}"
        report.append(entry)
        time.sleep(1.0)
    (RECON / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"recon: {len(report)}개 대상 → data/recon/report.json")
    for e in report:
        print(f"  {e['name']}: {e.get('status', e.get('error', e.get('skipped')))} "
              f"표 {len(e.get('tables', []))} xhr {len(e.get('xhrCandidates', []))}")


def collect():
    cfg = json.loads((HERE / "config.json").read_text(encoding="utf-8"))
    spec = _spec(cfg["list"]["url"], cfg["list"].get("method", "GET"), cfg["list"].get("data"))
    if cfg["list"].get("headers"):
        spec["headers"].update(cfg["list"]["headers"])
    ok, note = robots_allows(spec["url"])
    if not ok:
        raise SystemExit(f"robots.txt가 수집을 금지합니다: {note}")
    page_param = cfg["list"].get("pageParam", "pageIndex")
    pages = min(int(cfg["list"].get("pages", 10)), 300)
    delay = max(0.5, float(cfg.get("delayMs", 900)) / 1000)
    session = make_session(spec)

    rows, seen_rows, pairs, seen_ids = [], set(), [], set()
    det = cfg.get("detail") or {}
    rx = det.get("idRegex") or r"['\"]([0-9A-Za-z_\-]{4,40})['\"]"
    for pg in range(int(cfg["list"].get("startPage", 1)), int(cfg["list"].get("startPage", 1)) + pages):
        r = fetch(session, set_page(spec, page_param, pg), timeout=25)
        got, diag = parse_response(r)
        print(f"목록 p{pg}: {len(got)}행 ({diag.get('kind')})")
        fresh = []
        for row in got:
            k = (row["project"], row["recipient"], row["vendor"], row["amount"], row["date"])
            if k not in seen_rows:
                seen_rows.add(k)
                fresh.append(row)
        if det.get("urlTemplate"):
            for rec, pid in rows_with_ids(r.text, rx, det.get("idFrom", "onclick")):
                if pid not in seen_ids:
                    seen_ids.add(pid)
                    pairs.append((rec, pid))
        if not fresh:
            print("  새 행 없음 — 마지막 페이지로 판단")
            break
        rows.extend(fresh)
        time.sleep(delay)

    if det.get("urlTemplate"):
        cap = int(det.get("max", 200))
        print(f"상세 수집: {min(len(pairs), cap)}/{len(pairs)}건")
        for i, (listrec, pid) in enumerate(pairs[:cap]):
            url = det["urlTemplate"].replace("{id}", urllib.parse.quote(pid))
            try:
                dr = fetch(session, _spec(url), timeout=25)
            except Exception as e:
                print(f"  {pid}: {e}")
                continue
            inherit = {k: v for k, v in listrec.items() if v and k in
                       ("project", "recipient", "grantor", "bizno", "ceo", "grant")}
            inherit["note"] = f"공시ID:{pid}"
            tables = extract_tables(dr.text)
            best = next((t for t in tables if t["score"] >= 3 and len(t["rows"]) >= 3), None) \
                or (tables[0] if tables else None)
            if best and best["score"] >= 2:
                got = rows_from_table(best, extra=inherit)
                rows.extend(got)
                print(f"  {pid} ({inherit.get('project','?')}): {len(got)}행")
            time.sleep(delay)

    DATA.mkdir(exist_ok=True)
    (DATA / "disclosures.json").write_text(json.dumps(
        {"collectedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
         "source": cfg["list"]["url"], "rowCount": len(rows), "rows": rows},
        ensure_ascii=False), encoding="utf-8")
    ranked = analyze(rows)
    (DATA / "analysis.json").write_text(json.dumps(
        {"analyzedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
         "projectCount": len(ranked), "projects": ranked},
        ensure_ascii=False), encoding="utf-8")
    print(f"완료: 행 {len(rows)} / 사업 {len(ranked)} → data/disclosures.json, data/analysis.json")
    for p in ranked[:10]:
        print(f"  {p['prob']:>2}% {p['project'][:34]} / {p['recipient'][:16]} ({p['n']}건)")


if __name__ == "__main__":
    os.environ.setdefault("ALLOWED_HOSTS", ".go.kr,.or.kr")
    mode = sys.argv[1] if len(sys.argv) > 1 else "recon"
    (recon if mode == "recon" else collect)()
