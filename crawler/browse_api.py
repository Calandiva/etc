"""브라우저 세션 안에서 공시 데이터 JSON 엔드포인트를 직접 호출.
   POST /ea/retrieveInfoPblntfTrgetMngList.do (CSRF 없음, JSON 반환).
   requests는 서버가 차단하므로, 브라우저의 신뢰된 세션+jQuery로 $.ajax 호출한다."""
import json, os, sys, time, pathlib
HERE=pathlib.Path(__file__).resolve().parent; ROOT=HERE.parent
sys.path.insert(0, str(HERE))
from model import analyze
DATA=ROOT/"data"; RB=DATA/"recon-browser"
BASE="https://www.bojo.go.kr"
LIST_PAGE=BASE+"/ea/getEA001201View.do"
DATA_EP="/ea/retrieveInfoPblntfTrgetMngList.do"

def params(year, page, per=100, basis="2", jrsd="", lcgv="", sysse=""):
    return {"currentPageNum":str(page),"countPerPageNum":str(per),
        "fiscalyear":str(year),"bsnsyear":str(year),"jrsdCode":jrsd,
        "excInsttNm":"","ddtlbzNm":"","dcsnBeginDe":"","dcsnEndDe":"",
        "ifpbntSysSeCode":sysse,"sortOrder":"","searchFilterYn":"N",
        "basisCode":basis,"wdrLcgvCode":lcgv,"labSfrndCode":"",
        "selectedMultiText":"","selectedMultiType":"","selectedMultiSysSeCode":""}

def call(page, p, tries=3):
    js='''async (args)=>{
      const [ep, data] = args;
      const body = Object.entries(data).map(([k,v])=>encodeURIComponent(k)+'='+encodeURIComponent(v)).join('&');
      try {
        const res = await fetch(ep, {method:'POST', credentials:'same-origin',
          headers:{'Content-Type':'application/x-www-form-urlencoded; charset=UTF-8','X-Requested-With':'XMLHttpRequest'},
          body});
        const txt = await res.text();
        try { return {ok:true, status:res.status, json:JSON.parse(txt)}; }
        catch(e){ return {ok:false, status:res.status, text:txt.slice(0,500)}; }
      } catch(e){ return {ok:false, fetchError:String(e)}; }
    }'''
    last=None
    for _ in range(tries):
        r=page.evaluate(js, [DATA_EP, p])
        if r.get("ok") or (isinstance(r,dict) and "json" in r): return r
        last=r; page.wait_for_timeout(1200)
    return last or {"ok":False}

def find_list(o, depth=0):
    if depth>5: return None
    if isinstance(o,list) and o and isinstance(o[0],dict): return o
    if isinstance(o,dict):
        for v in o.values():
            r=find_list(v,depth+1)
            if r: return r
    return None

# 목록 레코드 → 표준 필드 (JSON 키 별칭)
ALIAS={"project":["ddtlbzNm","bsnsNm","사업명","보조사업명","sbsdBsnsNm"],
 "recipient":["excInsttNm","수행기관","보조사업자","기관명","insttNm"],
 "grantor":["jrsdNm","소관","중앙관서","교부기관","jrsdCodeNm"],
 "grant":["교부액","보조금액","sbsdAmt","txamt","totlAmt","aftrKeepAmt"],
 "bizno":["사업자","bizrno","brno"], "date":["dcsnDe","공시일"],
 "note":["hwipnBassId","dtlbzId","bsnsId"]}
def norm(s): return str(s or "").replace(" ","").lower()
def to_num(v):
    import re; m=re.sub(r"[^0-9.\-]","",str(v or "")); 
    try: return float(m) if m not in ("","-",".") else 0.0
    except: return 0.0
def rows_from_items(items, year):
    keys=list(items[0].keys())
    m={}
    for f,al in ALIAS.items():
        k=next((k for k in keys if norm(k) in {norm(a) for a in al}), None)
        if not k: k=next((k for k in keys if any(norm(a) in norm(k) for a in al)), None)
        if k: m[f]=k
    out=[]
    for it in items:
        rec={f:(to_num(it.get(m[f])) if f=="grant" else str(it.get(m[f],"")).strip()) for f in m}
        rec.setdefault("project",""); rec.setdefault("recipient","")
        rec["note"]=f"공시ID:{rec.get('note','')}|{year}"
        if rec.get("project") or rec.get("recipient"): out.append(rec)
    return out, m, keys

def run(mode="api"):
    from playwright.sync_api import sync_playwright
    cfg=json.loads((HERE/"config.json").read_text(encoding="utf-8")).get("browser",{}) if (HERE/"config.json").exists() else {}
    years=cfg.get("years",[2024,2023]); per=int(cfg.get("perPage",100))
    max_pages=int(cfg.get("maxPages",30)); delay=float(cfg.get("delayMs",700))/1000
    RB.mkdir(parents=True, exist_ok=True); DATA.mkdir(exist_ok=True)
    all_rows=[]; dbg=[]
    with sync_playwright() as pw:
        br=pw.chromium.launch(args=["--no-sandbox"])
        ctx=br.new_context(user_agent=os.environ.get("CRAWL_UA","SubsidyDisclosureResearch/1.0 (public data)"),locale="ko-KR")
        page=ctx.new_page(); page.set_default_timeout(45000)
        for attempt in range(5):
            try: page.goto(LIST_PAGE, wait_until="domcontentloaded", timeout=60000); page.wait_for_timeout(3500); break
            except Exception as e: print("goto 재시도",attempt+1,e); page.wait_for_timeout(3000)
        def read_opts(sel):
            try:
                return page.eval_on_selector(sel,
                  "el=>[...el.options].map(o=>[o.value,o.text]).filter(o=>o[0]&&o[0]!=='')") or []
            except Exception: return []
        def load_codes(basis, sel):
            # 국고/지방 라디오·select 선택 → change → 옵션 로드 대기
            try:
                page.evaluate("""(b)=>{
                  const set=el=>{if(!el)return; el.value=b; el.dispatchEvent(new Event('change',{bubbles:true}));};
                  set(document.querySelector('#EA001201Frm_basisCode, select[id$=_basisCode]'));
                  document.querySelectorAll('input[name*=basisCode],input[name*=ifpbntSysSe]').forEach(r=>{
                    if(r.value===b){r.checked=true;r.dispatchEvent(new Event('change',{bubbles:true}));}});
                }""", basis)
            except Exception: pass
            for _ in range(12):
                page.wait_for_timeout(700)
                o=read_opts(sel)
                if o: return o
            return read_opts(sel)
        jrsd=load_codes("1","#EA001201Frm_jrsdCode, select[id$=_jrsdCode]")
        lcgv=load_codes("2","#EA001201Frm_wdrLcgvCode, select[id$=_wdrLcgvCode]")
        (RB/"codes.json").write_text(json.dumps({"jrsd":jrsd,"lcgv":lcgv},ensure_ascii=False,indent=1),encoding="utf-8")
        print(f"중앙관서 {len(jrsd)}개, 지자체 {len(lcgv)}개")
        # 국고+지방 모두 시도. 국고는 중앙관서 필수라 basis=2(지방)부터, 국고는 sysse로 우회 시도
        plans=[]
        for y in years:
            for code,nm in (jrsd or [["",""]]):
                plans.append({"y":y,"basis":"1","jrsd":code,"lcgv":"","label":f"국고/{nm}"})
            for code,nm in (lcgv or []):
                plans.append({"y":y,"basis":"2","jrsd":"","lcgv":code,"label":f"지방/{nm}"})
        cfg_max=int(cfg.get("maxPlans",0))
        if cfg_max: plans=plans[:cfg_max]
        first_dump=False
        for pl in plans:
            y=pl["y"]; page_no=1
            while page_no<=max_pages:
                try:
                    r=call(page, params(y,page_no,per,pl["basis"],pl["jrsd"],pl["lcgv"]))
                except Exception as e:
                    dbg.append({"plan":pl["label"],"y":y,"page":page_no,"error":str(e)}); break
                if not r.get("ok"):
                    dbg.append({"plan":pl["label"],"page":page_no,"status":r.get("status"),"text":r.get("text","")[:150]}); break
                js=r["json"]
                if not first_dump:
                    (RB/"api-first.json").write_text(json.dumps(js,ensure_ascii=False)[:300000],encoding="utf-8"); first_dump=True
                err=js.get("ERROR-0000") if isinstance(js,dict) else None
                items=find_list(js)
                if not items:
                    if page_no==1: dbg.append({"plan":pl["label"],"y":y,"error0":err,"topkeys":list(js.keys())[:12] if isinstance(js,dict) else str(type(js))})
                    break
                rows,m,keys=rows_from_items(items,y)
                if page_no==1 and len(dbg)<3: dbg.append({"plan":pl["label"],"itemKeys":keys[:25],"mappedTo":m,"itemCount":len(items)})
                if not rows: break
                for rr in rows: rr["grantor"]=rr.get("grantor") or pl["label"]
                all_rows.extend(rows)
                print(f"{pl['label']} {y} p{page_no}: {len(rows)}행 (누적 {len(all_rows)})")
                if len(items)<per: break
                page_no+=1; time.sleep(delay)
            time.sleep(0.2)
        br.close()
    (RB/"api-debug.json").write_text(json.dumps(dbg,ensure_ascii=False,indent=1),encoding="utf-8")
    if all_rows:
        (DATA/"disclosures.json").write_text(json.dumps(
            {"collectedAt":time.strftime("%Y-%m-%dT%H:%M:%SZ",time.gmtime()),"source":LIST_PAGE,
             "rowCount":len(all_rows),"rows":all_rows},ensure_ascii=False),encoding="utf-8")
        ranked=analyze(all_rows)
        (DATA/"analysis.json").write_text(json.dumps(
            {"analyzedAt":time.strftime("%Y-%m-%dT%H:%M:%SZ",time.gmtime()),
             "projectCount":len(ranked),"projects":ranked},ensure_ascii=False),encoding="utf-8")
        print(f"완료: 행 {len(all_rows)} / 사업 {len(ranked)}")
    else:
        print("데이터 없음 — data/recon-browser/api-debug.json, api-first.json 확인")

if __name__=="__main__": run()
