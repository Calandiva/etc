"""헤드리스 브라우저 기반 공시 크롤러 (Playwright) — GitHub Actions 실행.

보조금통합포털 공시 목록은 JS(AJAX)로 표를 렌더링하므로 requests로는 데이터를 얻을 수 없다.
브라우저로 페이지를 실제 실행해 연도 조회 → 목록 표 → 개별 사업 상세 집행내역을 수집한다.

모드
  recon   — 목록 페이지를 열고 연도 조회를 시도한 뒤, 렌더된 DOM/스크린샷/표 구조를
            data/recon-browser/ 에 저장한다. (셀렉터 보정 근거)
  collect — config.json(browser 섹션)의 셀렉터대로 목록+상세를 수집해
            data/disclosures.json + data/analysis.json 생성.
"""
from __future__ import annotations
import json, os, re, sys, time, pathlib
HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
from model import analyze                                        # noqa: E402

DATA = ROOT / "data"
RB = DATA / "recon-browser"
LIST_URL = "https://www.bojo.go.kr/ea/getEA001201View.do"

ALIAS = {  # 표 헤더 → 표준 필드 (collect.py와 동일 사전 요약)
 "project":["보조사업명","사업명","세부사업명","내역사업명"],
 "grantor":["교부기관","소관기관","중앙관서","교부처","소관부처","지자체"],
 "recipient":["보조사업자","보조사업자명","수행기관","단체명","업체명","기관명"],
 "bizno":["사업자등록번호","사업자번호","고유번호"], "ceo":["대표자","대표자명","대표"],
 "item":["비목","비목명","계정과목","지출항목","세목"],
 "vendor":["거래처","거래처명","지급처","수취인","계약상대자","공급자","업체"],
 "vendorBizno":["거래처사업자번호","공급자사업자번호","계약상대자사업자번호"],
 "method":["계약방법","계약방식","구매방법","계약구분"],
 "amount":["집행액","지출액","금액","계약금액","지급액","집행금액","결제금액","지출금액"],
 "grant":["교부액","교부금액","보조금액","지원금액","예산액","총사업비"],
 "date":["집행일자","지출일자","거래일자","계약일자","지급일","일자","결제일"],
 "doc":["증빙","증빙유형","증빙서류"], "note":["비고","적요","내용","산출내역","성명"],
}
def _norm(s): return re.sub(r"[\s\"'()\[\]·:]","",str(s or "")).lower()
def _num(v):
    m=re.sub(r"[^0-9.\-]","",str(v or ""));  return float(m) if m not in("","-",".") else 0.0
def _map(headers):
    m={}
    for k,names in ALIAS.items():
        i=next((j for j,h in enumerate(headers) if _norm(h) in {_norm(n) for n in names}),-1)
        if i<0: i=next((j for j,h in enumerate(headers) if any(_norm(n) in _norm(h) for n in names)),-1)
        if i>=0: m[k]=i
    return m

def table_to_rows(headers, body, extra=None):
    m=_map(headers)
    if len(m)<2: return []
    out=[]
    for r in body:
        g=lambda k: (r[m[k]].strip() if m.get(k) is not None and m[k]<len(r) else "")
        rec={k:( _num(g(k)) if k in("amount","grant") else g(k)) for k in ALIAS}
        if extra:
            for k,v in extra.items():
                if not rec.get(k): rec[k]=v
        if rec["project"] or rec["recipient"] or rec["amount"]: out.append(rec)
    return out

def read_tables(page):
    """현재 페이지의 모든 표를 (headers, rows[]) 로 반환."""
    return page.evaluate("""() => {
      const out=[];
      for (const t of document.querySelectorAll('table')) {
        const trs=[...t.querySelectorAll('tr')].map(tr=>[...tr.querySelectorAll('th,td')].map(c=>c.innerText.trim()));
        const rows=trs.filter(r=>r.some(c=>c!==''));
        if (rows.length>=2) out.push({caption:(t.caption?.innerText||'').trim(), rows});
      }
      return out;
    }""")

def pick_data_table(tables):
    best,score=None,-1
    for t in tables:
        for hi in range(min(3,len(t["rows"]))):
            m=_map(t["rows"][hi])
            if len(m)>score: best,score=(t,hi,m),len(m)
    return best  # (table, header_idx, map) or None

def do_search(page, year):
    """연도 조회 시도 — select/input 채우고 조회 버튼 클릭."""
    js=f"""(y) => {{
      for (const sel of document.querySelectorAll('select')) {{
        for (const o of sel.options) if (o.value==String(y)||o.text.includes(String(y))) {{ sel.value=o.value; sel.dispatchEvent(new Event('change',{{bubbles:true}})); }}
      }}
      for (const nm of ['fiscalyear','bsnsyear']) {{
        const el=document.querySelector(`[name="${{nm}}"]`); if (el) {{ el.value=String(y); el.dispatchEvent(new Event('change',{{bubbles:true}})); }}
      }}
    }}"""
    page.evaluate(js, year)
    for label in ["조회","검색"]:
        try:
            btn=page.get_by_role("button", name=re.compile(label))
            if btn.count(): btn.first.click(timeout=4000); break
        except Exception: pass
    else:
        try: page.evaluate("() => { if (typeof f_search==='function') f_search(); }")
        except Exception: pass
    page.wait_for_timeout(2500)

def run(mode):
    from playwright.sync_api import sync_playwright
    cfg={}
    cfgp=HERE/"config.json"
    if cfgp.exists(): cfg=json.loads(cfgp.read_text(encoding="utf-8")).get("browser",{})
    years=cfg.get("years",[2024,2023])
    list_url=cfg.get("listUrl",LIST_URL)
    max_projects=int(cfg.get("maxProjects",120))
    delay=float(cfg.get("delayMs",700))/1000

    with sync_playwright() as pw:
        br=pw.chromium.launch(args=["--no-sandbox"])
        ctx=br.new_context(user_agent=os.environ.get("CRAWL_UA",
            "SubsidyDisclosureResearch/1.0 (public data; github actions)"), locale="ko-KR")
        page=ctx.new_page()
        page.set_default_timeout(45000)
        page.goto(list_url, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(2500)

        if mode=="recon":
            RB.mkdir(parents=True, exist_ok=True)
            page.screenshot(path=str(RB/"01-list.png"), full_page=True)
            (RB/"01-list.html").write_text(page.content(), encoding="utf-8")
            do_search(page, years[0])
            page.screenshot(path=str(RB/"02-searched.png"), full_page=True)
            (RB/"02-searched.html").write_text(page.content(), encoding="utf-8")
            tables=read_tables(page)
            report={"url":list_url,"year":years[0],"tableCount":len(tables),
                    "tables":[{"caption":t["caption"],"headerGuess":t["rows"][0][:14],
                               "rowCount":len(t["rows"]),
                               "sample":t["rows"][1][:14] if len(t["rows"])>1 else []} for t in tables],
                    "selects":page.eval_on_selector_all("select",
                      "els=>els.map(e=>({name:e.name,options:[...e.options].slice(0,6).map(o=>o.text)}))"),
                    "buttons":page.eval_on_selector_all("button,a.btn,a.button",
                      "els=>els.slice(0,40).map(e=>e.innerText.trim()).filter(Boolean)")}
            (RB/"report.json").write_text(json.dumps(report,ensure_ascii=False,indent=1),encoding="utf-8")
            print(f"browser recon: 표 {len(tables)}개 → data/recon-browser/")
            for t in report["tables"]: print("  헤더:",t["headerGuess"])
            br.close(); return

        # collect
        all_rows=[]
        for y in years:
            do_search(page, y)
            picked=pick_data_table(read_tables(page))
            if not picked:
                print(f"{y}년: 목록 표를 찾지 못함"); continue
            tbl,hi,m=picked
            headers=tbl["rows"][hi]
            print(f"{y}년 목록: {len(tbl['rows'])-hi-1}행, 컬럼 {list(m)}")
            # 목록 표의 각 행을 상세로 진입 (행 클릭 → 상세 표 수집 → 뒤로)
            row_count=len(tbl["rows"])-hi-1
            list_rows=table_to_rows(headers, tbl["rows"][hi+1:])
            all_rows.extend(list_rows)
            sel=cfg.get("rowSelector") or "table tr"
            for idx in range(min(row_count, max_projects)):
                try:
                    do_search(page, y)  # 목록 복귀 후 재조회
                    rows=page.query_selector_all(sel)
                    data_rows=[r for r in rows if r.query_selector("td")]
                    if idx>=len(data_rows): break
                    target=data_rows[idx]
                    inherit=None
                    if idx<len(list_rows): 
                        lr=list_rows[idx]
                        inherit={k:v for k,v in lr.items() if v and k in ("project","recipient","grantor","bizno","ceo","grant")}
                    target.click(timeout=5000)
                    page.wait_for_timeout(int(delay*1000)+800)
                    picked2=pick_data_table([t for t in read_tables(page) if len(t["rows"])>=4])
                    if picked2:
                        t2,h2,m2=picked2
                        det=table_to_rows(t2["rows"][h2], t2["rows"][h2+1:], extra=(inherit or {}))
                        if det:
                            all_rows.extend(det)
                            print(f"  [{idx+1}] {(inherit or {}).get('project','?')[:24]}: {len(det)}행")
                    page.go_back(wait_until="domcontentloaded")
                    page.wait_for_timeout(400)
                except Exception as e:
                    print(f"  [{idx+1}] 건너뜀: {e}")
                    try: page.goto(list_url, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(2500)
                    except Exception: pass
        DATA.mkdir(exist_ok=True)
        (DATA/"disclosures.json").write_text(json.dumps(
            {"collectedAt":time.strftime("%Y-%m-%dT%H:%M:%SZ",time.gmtime()),
             "source":list_url,"rowCount":len(all_rows),"rows":all_rows},ensure_ascii=False),encoding="utf-8")
        ranked=analyze(all_rows)
        (DATA/"analysis.json").write_text(json.dumps(
            {"analyzedAt":time.strftime("%Y-%m-%dT%H:%M:%SZ",time.gmtime()),
             "projectCount":len(ranked),"projects":ranked},ensure_ascii=False),encoding="utf-8")
        print(f"완료: 행 {len(all_rows)} / 사업 {len(ranked)}")
        br.close()

if __name__=="__main__":
    run(sys.argv[1] if len(sys.argv)>1 else "recon")
