"""공시 목록 데이터 엔드포인트 직접 탐침 (requests, 브라우저 불필요).
   POST /ea/retrieveInfoPblntfTrgetMngList.do — #EA001201FrmSrch serialize, JSON 반환, CSRF 없음."""
import json, os, sys, time, pathlib
import requests
HERE=pathlib.Path(__file__).resolve().parent; ROOT=HERE.parent
DATA=ROOT/"data"; RB=DATA/"recon-browser"
BASE="https://www.bojo.go.kr"
LIST_PAGE=BASE+"/ea/getEA001201View.do"
DATA_EP=BASE+"/ea/retrieveInfoPblntfTrgetMngList.do"
UA=os.environ.get("CRAWL_UA","SubsidyDisclosureResearch/1.0 (public data; github actions)")

def base_params(year, page=1, per=100, basis="2", jrsd="", sysse=""):
    return {
        "currentPageNum":str(page), "countPerPageNum":str(per),
        "fiscalyear":str(year), "bsnsyear":str(year),
        "jrsdCode":jrsd, "excInsttNm":"", "ddtlbzNm":"",
        "dcsnBeginDe":"", "dcsnEndDe":"", "ifpbntSysSeCode":sysse,
        "sortOrder":"", "searchFilterYn":"N",
        "basisCode":basis, "wdrLcgvCode":"", "labSfrndCode":"",
        "selectedMultiText":"", "selectedMultiType":"", "selectedMultiSysSeCode":"",
    }

def main():
    RB.mkdir(parents=True, exist_ok=True)
    s=requests.Session()
    s.headers.update({"User-Agent":UA,
        "X-Requested-With":"XMLHttpRequest",
        "Content-Type":"application/x-www-form-urlencoded; charset=UTF-8",
        "Referer":LIST_PAGE, "Origin":BASE,
        "Accept":"application/json, text/javascript, */*; q=0.01"})
    # 세션 쿠키 확보
    try:
        r0=s.get(LIST_PAGE, timeout=25); print("list page:", r0.status_code, "cookies:", list(s.cookies.keys()))
    except Exception as e:
        print("list page 실패:", e)

    combos=[
        ("지방2024", base_params(2024, basis="2")),
        ("지방2023", base_params(2023, basis="2")),
        ("국고2024전체sys", base_params(2024, basis="1", sysse="002")),
        ("국고2024", base_params(2024, basis="1")),
    ]
    report=[]
    for name,params in combos:
        try:
            r=s.post(DATA_EP, data=params, timeout=30)
            body=r.text
            entry={"combo":name,"status":r.status_code,"ct":r.headers.get("content-type",""),"len":len(body)}
            js=None
            try: js=r.json()
            except Exception: pass
            if js is not None:
                # 상위 키/리스트 탐색
                def summarize(o,depth=0):
                    if depth>3: return None
                    if isinstance(o,list): return {"list_len":len(o),"first":(o[0] if o else None)}
                    if isinstance(o,dict):
                        out={}
                        for k,v in list(o.items())[:20]:
                            if isinstance(v,list): out[k]=f"list({len(v)})"+((" e.g. "+json.dumps(v[0],ensure_ascii=False)[:200]) if v else "")
                            elif isinstance(v,(dict,)): out[k]="dict"
                            else: out[k]=str(v)[:40]
                        return out
                    return str(o)[:100]
                entry["json_keys"]=summarize(js)
                (RB/f"api-{name}.json").write_text(json.dumps(js,ensure_ascii=False)[:200000], encoding="utf-8")
            else:
                entry["body_head"]=body[:300]
            report.append(entry)
            print(f"{name}: {r.status_code} {entry.get('ct','')[:30]} len={len(body)} json={'O' if js is not None else 'X'}")
        except Exception as e:
            report.append({"combo":name,"error":str(e)}); print(f"{name}: ERR {e}")
        time.sleep(1.0)
    (RB/"api-probe.json").write_text(json.dumps(report,ensure_ascii=False,indent=1), encoding="utf-8")
    print("→ data/recon-browser/api-probe.json + api-*.json")

if __name__=="__main__": main()
