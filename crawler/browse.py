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

def _goto(page, url, tries=3):
    last=None
    for i in range(tries):
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(2500); return
        except Exception as e:
            last=e; print(f"  goto 재시도 {i+1}/{tries}: {e}")
            page.wait_for_timeout(3000)
    raise last

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
    """회계연도·사업연도(숨은 필드/보이는 select)를 year로 맞추고 조회, 결과 렌더 대기."""
    page.evaluate("""(y)=>{
      document.querySelectorAll('input[name=fiscalyear],input[name=bsnsyear],input[id*=fsyr],input[id*=bsnsyear]')
        .forEach(el=>{el.value=String(y);});
      document.querySelectorAll('select').forEach(el=>{
        if(/fsyr|bsnsyear|회계연도|사업연도/i.test((el.id||'')+' '+(el.name||''))){
          for(const o of el.options) if(o.value==String(y)||o.text.includes(String(y))){el.value=o.value;}
          el.dispatchEvent(new Event('change',{bubbles:true}));
        }});
    }""", year)
    # 보이는 조회 버튼 클릭 → 실패 시 전역 f_search()
    ok=False
    for loc in ["button.searchButton:visible", "a.searchButton:visible",
                "button:has-text('조회'):visible", "input[value='조회']:visible"]:
        try:
            el=page.locator(loc).first
            if el.count() and el.is_visible():
                el.click(timeout=4000); ok=True; break
        except Exception: pass
    if not ok:
        for fn in ["f_search", "fn_search", "goSearch"]:
            try:
                if page.evaluate(f"()=>typeof {fn}==='function'"):
                    page.evaluate(f"()=>{fn}()"); ok=True; break
            except Exception: pass
    # 결과 컨테이너가 채워질 때까지 대기(카드형 #tableWrap 또는 표형 전환)
    try:
        page.wait_for_function(
          "()=>{const w=document.querySelector('#tableWrap');"
          "const rows=document.querySelectorAll('#tableWrap li,#tableWrap tr,#tableWrap .card,#tableWrap [class*=item]');"
          "return (w&&w.innerText.trim().length>30)||rows.length>0;}", timeout=20000)
    except Exception:
        page.wait_for_timeout(3000)

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
        netlog=[]
        def on_resp(resp):
            try:
                u=resp.url
                if resp.request.resource_type in ("xhr","fetch") or ".do" in u:
                    netlog.append({"url":u,"status":resp.status,
                        "type":resp.request.resource_type,"method":resp.request.method,
                        "postData":(resp.request.post_data or "")[:300],
                        "ct":resp.headers.get("content-type","")})
            except Exception: pass
        page.on("response", on_resp)
        dialogs=[]
        page.on("dialog", lambda d:(dialogs.append(d.message), d.accept()))
        consolelog=[]
        page.on("console", lambda m: consolelog.append(f"{m.type}: {m.text}"[:200]))
        page.goto(list_url, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(3000)

        if mode=="recon":
            # 연도 옵션이 로드될 때까지 대기
            try:
                page.wait_for_function(
                  "()=>{const s=document.querySelector('[id*=fsyr],[name*=fsyr],[name=bsnsyear]');return s&&s.options&&s.options.length>0;}",
                  timeout=10000)
            except Exception: pass
            year_opts=page.eval_on_selector_all("select",
              "els=>els.map(e=>({id:e.id,name:e.name,opts:[...e.options].map(o=>o.value+':'+o.text).slice(0,8)}))")
            RB.mkdir(parents=True, exist_ok=True)
            page.screenshot(path=str(RB/"01-list.png"), full_page=True)
            (RB/"01-list.html").write_text(page.content(), encoding="utf-8")
            # 조회 전 후보 버튼 가시성 진단
            search_btns=page.eval_on_selector_all("button,a,input[type=button],input[type=submit]",
              "els=>els.filter(e=>/조회|검색/.test(e.innerText||e.value||'')).map(e=>({tag:e.tagName,cls:e.className.slice(0,40),vis:e.offsetParent!==null,oc:(e.getAttribute('onclick')||'').slice(0,60)}))")
            has_fsearch=page.evaluate("()=>({f_search:typeof f_search,fn_search:typeof window.fn_search,keys:Object.keys(window).filter(k=>/search/i.test(k)).slice(0,10)})")
            do_search(page, years[0])
            page.screenshot(path=str(RB/"02-searched.png"), full_page=True)
            (RB/"02-searched.html").write_text(page.content(), encoding="utf-8")
            # 결과를 표형으로 전환 시도(집계 파싱 쉬움)
            try:
                page.evaluate("()=>{const s=document.querySelector('#sbxGridDiv,select[id*=GridDiv]');"
                              "if(s){s.value='1';s.dispatchEvent(new Event('change',{bubbles:true}));}}")
                page.wait_for_timeout(2000)
            except Exception: pass
            # 결과 컨테이너 원문 저장(카드형 결과 구조 확인용)
            try:
                tw=page.eval_on_selector("#tableWrap","el=>el.innerHTML")
                (RB/"03-tableWrap.html").write_text(tw or "", encoding="utf-8")
            except Exception as e:
                (RB/"03-tableWrap.html").write_text(f"(#tableWrap 없음: {e})", encoding="utf-8")
            # 결과 링크(상세 진입) 후보
            links=page.eval_on_selector_all("#tableWrap a, #tableWrap [onclick], #tableWrap button",
              "els=>els.slice(0,20).map(e=>({t:e.innerText.trim().slice(0,40),oc:(e.getAttribute('onclick')||'').slice(0,120),href:(e.getAttribute('href')||'').slice(0,80)}))")
            tables=read_tables(page)
            report={"url":list_url,"year":years[0],"tableCount":len(tables),
                    "netlog":netlog,"yearOptions":year_opts,"dialogs":dialogs,"console":consolelog[-30:],
                    "searchButtons":search_btns,"searchFns":has_fsearch,
                    "resultLinks":links,
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
        RB.mkdir(parents=True, exist_ok=True)
        all_rows=[]
        for yi,y in enumerate(years):
            do_search(page, y)
            if yi==0:
                try:
                    (RB/"collect-tableWrap.html").write_text(
                        page.eval_on_selector("#tableWrap","el=>el.innerHTML") or "", encoding="utf-8")
                    page.screenshot(path=str(RB/"collect-searched.png"), full_page=True)
                except Exception: pass
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
                    try:
                        page.goto(list_url, wait_until="domcontentloaded", timeout=60000)
                        page.wait_for_timeout(1500)
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
