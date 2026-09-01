"""브라우저 세션 안에서 공시 JSON 엔드포인트 직접 호출 + 공시일 구간 분할 수집.
   POST /ea/retrieveInfoPblntfTrgetMngList.do (CSRF 없음, JSON).
   '조회 범위 초과'는 공시일(dcsnBeginDe~dcsnEndDe) 구간을 반으로 쪼개 회피한다."""
import json, os, re, sys, time, pathlib, datetime
HERE=pathlib.Path(__file__).resolve().parent; ROOT=HERE.parent
sys.path.insert(0, str(HERE))
from model import analyze
DATA=ROOT/"data"; RB=DATA/"recon-browser"
BASE="https://www.bojo.go.kr"; LIST_PAGE=BASE+"/ea/getEA001201View.do"
DATA_EP="/ea/retrieveInfoPblntfTrgetMngList.do"
FETCH_JS = (
 "async (a)=>{const[ep,data]=a;"
 "const body=Object.entries(data).map(([k,v])=>encodeURIComponent(k)+'='+encodeURIComponent(v)).join('&');"
 "try{const r=await fetch(ep,{method:'POST',credentials:'include',"
 "headers:{'Content-Type':'application/x-www-form-urlencoded; charset=UTF-8','X-Requested-With':'XMLHttpRequest'},body});"
 "const t=await r.text();try{return{ok:true,status:r.status,json:JSON.parse(t)};}"
 "catch(e){return{ok:false,status:r.status,text:t.slice(0,300)};}}"
 "catch(e){return{ok:false,fetchError:String(e)};}}")

def params(year, page, per, basis, beg, end):
    return {"currentPageNum":str(page),"countPerPageNum":str(per),
        "fiscalyear":str(year),"bsnsyear":str(year),"jrsdCode":"",
        "excInsttNm":"","ddtlbzNm":"","dcsnBeginDe":beg,"dcsnEndDe":end,
        "ifpbntSysSeCode":"","sortOrder":"","searchFilterYn":"N",
        "basisCode":basis,"wdrLcgvCode":"","labSfrndCode":"",
        "selectedMultiText":"","selectedMultiType":"","selectedMultiSysSeCode":""}

def call(page, p, tries=3):
    last=None
    for _ in range(tries):
        r=page.evaluate(FETCH_JS,[DATA_EP,p])
        if isinstance(r,dict) and r.get("ok"): return r
        last=r; page.wait_for_timeout(1000)
    return last or {"ok":False}

def find_list(o,d=0):
    if d>5: return None
    if isinstance(o,list) and o and isinstance(o[0],dict): return o
    if isinstance(o,dict):
        for v in o.values():
            r=find_list(v,d+1)
            if r: return r
    return None

ALIAS={"project":["ddtlbznm","bsnsnm","사업명","보조사업명","sbsdbsnsnm","dtlbznm"],
 "recipient":["excinsttnm","수행기관","보조사업자","기관명","insttnm","excinstt"],
 "grantor":["jrsdnm","소관","중앙관서","교부기관","jrsdcodenm","wdrlcgvnm"],
 "grant":["교부액","보조금액","sbsdamt","totlamt","aftrkeepamt","txamt","bsnsamt","sumamt"],
 "bizno":["bizrno","brno","사업자"], "date":["dcsnde","공시일"],
 "note":["hwipnbassid","dtlbzid","bsnsid","infopblntftrgetmngid"]}
def nk(s): return re.sub(r"[^0-9a-z가-힣]","",str(s or "").lower())
def to_num(v):
    m=re.sub(r"[^0-9.\-]","",str(v or ""))
    try: return float(m) if m not in("","-",".") else 0.0
    except: return 0.0
def map_items(items):
    keys=list(items[0].keys()); m={}
    for f,al in ALIAS.items():
        k=next((k for k in keys if nk(k) in {nk(a) for a in al}),None)
        if not k: k=next((k for k in keys if any(nk(a) in nk(k) for a in al)),None)
        if k: m[f]=k
    return m,keys
def to_rows(items,year,basis,m):
    out=[]
    for it in items:
        rec={f:(to_num(it.get(m[f])) if f=="grant" else str(it.get(m[f],"")).strip()) for f in m}
        rec.setdefault("project",""); rec.setdefault("recipient","")
        rec["note"]=f"공시ID:{rec.get('note','')}"
        rec["_basis"]=basis; rec["_year"]=year
        if rec.get("project") or rec.get("recipient"): out.append(rec)
    return out

def daterange_windows(y):
    # 공시일은 회계연도 종료 후 이듬해 상반기 집중. 넓게 잡고 분할로 좁힌다.
    return (f"{y}0101", f"{y+2}1231")

def run(mode="api"):
    from playwright.sync_api import sync_playwright
    cfg=json.loads((HERE/"config.json").read_text(encoding="utf-8")).get("browser",{}) if (HERE/"config.json").exists() else {}
    years=cfg.get("years",[2024,2023]); per=int(cfg.get("perPage",100))
    max_pages=int(cfg.get("maxPages",50)); delay=float(cfg.get("delayMs",600))/1000
    min_days=int(cfg.get("minWindowDays",3)); max_queries=int(cfg.get("maxQueries",400))
    RB.mkdir(parents=True,exist_ok=True); DATA.mkdir(exist_ok=True)
    all_rows=[]; dbg=[]; dumped=[False]; qcount=[0]
    def d(s): return datetime.datetime.strptime(s,"%Y%m%d").date()
    def s(dt): return dt.strftime("%Y%m%d")

    with sync_playwright() as pw:
        br=pw.chromium.launch(args=["--no-sandbox"])
        ctx=br.new_context(user_agent=os.environ.get("CRAWL_UA","SubsidyDisclosureResearch/1.0 (public data)"),locale="ko-KR")
        page=ctx.new_page(); page.set_default_timeout(45000)
        caught=[]
        def on_resp(resp):
            try:
                u=resp.url
                if ".do" in u and resp.request.method in ("GET","POST"):
                    ct=resp.headers.get("content-type","")
                    if "json" in ct or "text" in ct:
                        b=resp.text()
                        if any(x in b for x in ["jrsd","Jrsd","중앙관서","관서","lcgv","Lcgv","지자체","시도","코드","Code"]) and len(b)<200000:
                            caught.append({"url":u[:120],"len":len(b),"body":b[:8000]})
            except Exception: pass
        page.on("response", on_resp)
        for a in range(5):
            try: page.goto(LIST_PAGE,wait_until="domcontentloaded",timeout=60000); page.wait_for_timeout(4000); break
            except Exception as e: print("goto 재시도",a+1,e); page.wait_for_timeout(3000)
        # 국고 라디오/구분 선택을 시도해 관서 콤보 로드 유발
        try:
            page.evaluate("""()=>{
              document.querySelectorAll('input[type=radio]').forEach(r=>{if(/001|국고/.test(r.value+r.name)){r.checked=true;r.dispatchEvent(new Event('click',{bubbles:true}));r.dispatchEvent(new Event('change',{bubbles:true}));}});
              const s=document.querySelector('#EA001201Frm_basisCode,select[id$=_basisCode]');if(s){s.value='1';s.dispatchEvent(new Event('change',{bubbles:true}));}
            }""")
            page.wait_for_timeout(4000)
        except Exception: pass
        (RB/"combo-responses.json").write_text(json.dumps(caught,ensure_ascii=False,indent=1)[:400000],encoding="utf-8")
        print("콤보 후보 응답",len(caught),"건")

        def collect_window(year, basis, beg, end):
            """[beg,end] 창을 수집. 범위 초과면 반으로 분할 재귀."""
            if qcount[0]>=max_queries: return
            qcount[0]+=1
            r=call(page, params(year,1,per,basis,beg,end))
            if not dumped[0]:
                (RB/"api-first.json").write_text(json.dumps(r,ensure_ascii=False)[:200000],encoding="utf-8"); dumped[0]=True
            if not isinstance(r,dict) or not r.get("ok"):
                dbg.append({"y":year,"basis":basis,"win":[beg,end],"fail":str(r)[:150]}); return
            js=r["json"]; err=js.get("ERROR-0000") if isinstance(js,dict) else None
            items=find_list(js)
            if err and ("초과" in err or "범위" in err) and not items:
                b,e=d(beg),d(end)
                if (e-b).days<=min_days:
                    dbg.append({"y":year,"basis":basis,"win":[beg,end],"note":"최소창인데 초과 — 스킵"}); return
                mid=b+(e-b)/2
                collect_window(year,basis,beg,s(mid)); time.sleep(delay)
                collect_window(year,basis,s(mid+datetime.timedelta(days=1)),end); return
            if not items:
                if len(dbg)<12: dbg.append({"y":year,"basis":basis,"win":[beg,end],"err":err,"topkeys":list(js.keys())[:12] if isinstance(js,dict) else str(type(js))})
                return
            m,keys=map_items(items)
            if len([x for x in dbg if x.get("mappedTo")])<2:
                dbg.append({"y":year,"basis":basis,"itemKeys":keys[:30],"mappedTo":m,"count":len(items)})
            all_rows.extend(to_rows(items,year,basis,m))
            # 페이지네이션
            pageno=2
            while len(items)>=per and pageno<=max_pages and qcount[0]<max_queries:
                qcount[0]+=1; time.sleep(delay)
                r2=call(page, params(year,pageno,per,basis,beg,end))
                if not (isinstance(r2,dict) and r2.get("ok")): break
                items=find_list(r2["json"]) or []
                if not items: break
                all_rows.extend(to_rows(items,year,basis,m)); pageno+=1
            print(f"{year} basis{basis} [{beg}-{end}]: 누적 {len(all_rows)}행 (q{qcount[0]})")

        for y in years:
            beg,end=daterange_windows(y)
            for basis in ["1","2"]:
                collect_window(y, basis, beg, end); time.sleep(delay)
        br.close()

    (RB/"api-debug.json").write_text(json.dumps(dbg,ensure_ascii=False,indent=1),encoding="utf-8")
    # 중복 제거
    seen=set(); uniq=[]
    for r in all_rows:
        k=(r.get("project"),r.get("recipient"),r.get("note"),r.get("_year"))
        if k not in seen: seen.add(k); uniq.append(r)
    if uniq:
        (DATA/"disclosures.json").write_text(json.dumps(
            {"collectedAt":time.strftime("%Y-%m-%dT%H:%M:%SZ",time.gmtime()),"source":LIST_PAGE,
             "rowCount":len(uniq),"rows":uniq},ensure_ascii=False),encoding="utf-8")
        ranked=analyze(uniq)
        (DATA/"analysis.json").write_text(json.dumps(
            {"analyzedAt":time.strftime("%Y-%m-%dT%H:%M:%SZ",time.gmtime()),
             "projectCount":len(ranked),"projects":ranked},ensure_ascii=False),encoding="utf-8")
        print(f"완료: 수집 {len(uniq)}행 / 사업 {len(ranked)} / 쿼리 {qcount[0]}")
    else:
        print(f"데이터 없음 (쿼리 {qcount[0]}) — api-debug.json / api-first.json 확인")

if __name__=="__main__": run()
