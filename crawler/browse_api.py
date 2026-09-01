"""bojo.go.kr 공시 수집 (브라우저 세션 안에서 실제 엔드포인트 직접 호출).

f_search/EA001201.js 소스 분석으로 확정한 사실:
  · 검색 JSON  : POST /ea/retrieveInfoPblntfTrgetMngList.do  (유효 자치단체/관서 코드 없으면 '조회 범위 초과')
  · 자치단체코드: POST /ea/getWdrLcgvCodeList.do  (basisCode=2)
  · 엑셀 다운로드: POST /ea/retrieveInfoPblntfTrgetMngListExcelDownload.do
requests는 서버가 차단하므로 브라우저의 신뢰 세션 fetch로 호출한다.
"""
import json, os, re, sys, time, pathlib
HERE = pathlib.Path(__file__).resolve().parent; ROOT = HERE.parent
sys.path.insert(0, str(HERE))
from model import analyze
DATA = ROOT/"data"; RB = DATA/"recon-browser"
BASE = "https://www.bojo.go.kr"
LIST_PAGE = BASE + "/ea/getEA001201View.do"
SEARCH_EP = "/ea/retrieveInfoPblntfTrgetMngList.do"
LCGV_EP   = "/ea/getWdrLcgvCodeList.do"

def cfgget():
    p = HERE/"config.json"
    return (json.loads(p.read_text(encoding="utf-8")).get("browser", {}) if p.exists() else {})

def find_list(o, d=0):
    if d > 6: return None
    if isinstance(o, list) and o and isinstance(o[0], dict): return o
    if isinstance(o, dict):
        for v in o.values():
            r = find_list(v, d+1)
            if r: return r
    return None

ALIAS = {"project":["ddtlbznm","bsnsnm","사업명","보조사업명","sbsdbsnsnm","dtlbznm","bojosaeopmyeong"],
 "recipient":["excinsttnm","수행기관","보조사업자","기관명","insttnm"],
 "grantor":["jrsdnm","소관","중앙관서","교부기관","wdrlcgvnm","자치단체"],
 "grant":["교부액","보조금액","sbsdamt","totlamt","aftrkeepamt","txamt","bsnsamt","sumamt","gyobuaek"],
 "bizno":["bizrno","brno","사업자"], "date":["dcsnde","공시일"],
 "note":["hwipnbassid","dtlbzid","bsnsid","infopblntftrgetmngid"]}
def nk(s): return re.sub(r"[^0-9a-z가-힣]", "", str(s or "").lower())
def to_num(v):
    m = re.sub(r"[^0-9.\-]", "", str(v or ""))
    try: return float(m) if m not in ("", "-", ".") else 0.0
    except ValueError: return 0.0
def map_items(items):
    keys = list(items[0].keys()); m = {}
    for f, al in ALIAS.items():
        k = next((k for k in keys if nk(k) in {nk(a) for a in al}), None)
        if not k: k = next((k for k in keys if any(nk(a) in nk(k) for a in al)), None)
        if k: m[f] = k
    return m, keys
def to_rows(items, year, basis, m, label):
    out = []
    for it in items:
        rec = {f: (to_num(it.get(m[f])) if f == "grant" else str(it.get(m[f], "")).strip()) for f in m}
        rec.setdefault("project", ""); rec.setdefault("recipient", "")
        rec["note"] = "공시ID:" + rec.get("note", "")
        rec["_year"] = year; rec["_basis"] = basis
        if not rec.get("grantor"): rec["grantor"] = label
        if rec.get("project") or rec.get("recipient"): out.append(rec)
    return out

FETCH = ("async (a)=>{const[ep,body]=a;try{"
 "const r=await fetch(ep,{method:'POST',credentials:'include',"
 "headers:{'Content-Type':'application/x-www-form-urlencoded; charset=UTF-8','X-Requested-With':'XMLHttpRequest'},body});"
 "const t=await r.text();try{return{ok:true,status:r.status,json:JSON.parse(t)};}"
 "catch(e){return{ok:false,status:r.status,text:t.slice(0,300)};}}catch(e){return{ok:false,err:String(e)};}}")

def post(page, ep, params, tries=3):
    body = "&".join(f"{k}={v}" for k, v in params.items())
    last = None
    for _ in range(tries):
        r = page.evaluate(FETCH, [ep, body])
        if isinstance(r, dict) and r.get("ok"): return r
        last = r; page.wait_for_timeout(1000)
    return last or {"ok": False}

def search_params(year, page_no, per, basis, lcgv):
    return {"currentPageNum": str(page_no), "nPageSize": str(per), "countPerPageNum": str(per),
        "fiscalyear": str(year), "bsnsyear": str(year), "jrsdCode": "",
        "excInsttNm": "", "ddtlbzNm": "", "dcsnBeginDe": "", "dcsnEndDe": "",
        "ifpbntSysSeCode": "", "sortOrder": "", "searchFilterYn": "N",
        "basisCode": basis, "wdrLcgvCode": lcgv, "labSfrndCode": "",
        "selectedMultiText": "", "selectedMultiType": "", "selectedMultiSysSeCode": ""}

def run(mode="api"):
    from playwright.sync_api import sync_playwright
    cfg = cfgget()
    years = cfg.get("years", [2024, 2023]); per = int(cfg.get("perPage", 100))
    max_pages = int(cfg.get("maxPages", 50)); delay = float(cfg.get("delayMs", 500))/1000
    max_codes = int(cfg.get("maxCodes", 0))
    RB.mkdir(parents=True, exist_ok=True); DATA.mkdir(exist_ok=True)
    all_rows = []; dbg = []

    with sync_playwright() as pw:
        br = pw.chromium.launch(args=["--no-sandbox"])
        ctx = br.new_context(user_agent=os.environ.get("CRAWL_UA", "SubsidyDisclosureResearch/1.0 (public data)"), locale="ko-KR")
        page = ctx.new_page(); page.set_default_timeout(45000)
        for a in range(5):
            try:
                page.goto(LIST_PAGE, wait_until="domcontentloaded", timeout=60000); page.wait_for_timeout(3000); break
            except Exception as e:
                print("goto 재시도", a+1, e); page.wait_for_timeout(3000)

        # 1) 자치단체 코드 목록 확보
        codes = []
        r = post(page, LCGV_EP, {"basisCode": "2"})
        (RB/"lcgv-codes.json").write_text(json.dumps(r, ensure_ascii=False)[:120000], encoding="utf-8")
        items = find_list(r.get("json")) if isinstance(r, dict) and r.get("json") else None
        if items:
            # 코드/이름 키 탐지
            k0 = list(items[0].keys())
            ck = next((k for k in k0 if re.search(r"code|cd", k, re.I)), k0[0])
            nk_ = next((k for k in k0 if re.search(r"nm|name|텍스트|text", k, re.I)), (k0[1] if len(k0) > 1 else k0[0]))
            codes = [(str(it.get(ck, "")).strip(), str(it.get(nk_, "")).strip()) for it in items if str(it.get(ck, "")).strip()]
        print("자치단체 코드:", len(codes), codes[:5])
        dbg.append({"lcgvCount": len(codes), "sample": codes[:5]})
        if max_codes: codes = codes[:max_codes]

        # 2) 코드별 지방보조사업 검색 (유효 코드 → 조회상한 회피)
        for code, nm in codes:
            for y in years:
                page_no = 1
                while page_no <= max_pages:
                    rr = post(page, SEARCH_EP, search_params(y, page_no, per, "2", code))
                    js = rr.get("json") if isinstance(rr, dict) else None
                    err = js.get("ERROR-0000") if isinstance(js, dict) else None
                    its = find_list(js) if js else None
                    if not its:
                        if page_no == 1 and len([d for d in dbg if d.get("firstErr")]) < 6:
                            dbg.append({"code": code, "nm": nm, "y": y, "firstErr": err})
                        break
                    m, keys = map_items(its)
                    if not any(d.get("mappedTo") for d in dbg):
                        dbg.append({"mappedTo": m, "itemKeys": keys[:30]})
                    got = to_rows(its, y, "2", m, f"지방/{nm}")
                    all_rows.extend(got)
                    if page_no == 1:
                        print(f"지방 {nm}({code}) {y}: {len(got)}행 (누적 {len(all_rows)})")
                    if len(its) < per: break
                    page_no += 1; time.sleep(delay)
                time.sleep(delay*0.5)
        br.close()

    # 중복 제거
    seen = set(); uniq = []
    for r in all_rows:
        k = (r.get("project"), r.get("recipient"), r.get("note"), r.get("_year"))
        if k not in seen: seen.add(k); uniq.append(r)
    (RB/"api-debug.json").write_text(json.dumps(dbg, ensure_ascii=False, indent=1), encoding="utf-8")
    if uniq:
        (DATA/"disclosures.json").write_text(json.dumps(
            {"collectedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "source": LIST_PAGE,
             "rowCount": len(uniq), "rows": uniq}, ensure_ascii=False), encoding="utf-8")
        ranked = analyze(uniq)
        (DATA/"analysis.json").write_text(json.dumps(
            {"analyzedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
             "projectCount": len(ranked), "projects": ranked}, ensure_ascii=False), encoding="utf-8")
        print(f"완료: 수집 {len(uniq)}행 / 사업 {len(ranked)}")
    else:
        print("데이터 없음 — data/recon-browser/api-debug.json, lcgv-codes.json 확인")

if __name__ == "__main__":
    run()
