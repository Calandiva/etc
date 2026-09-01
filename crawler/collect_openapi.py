"""공공데이터포털 국고보조금 OpenAPI로 보조사업 정보를 자동 수집 → 요건 모델 분석.
   bojo.go.kr 공시 검색은 헤드리스 자동화를 차단하므로(커스텀 위젯·name 없는 폼·조회상한),
   정부가 대량 조회용으로 공식 제공하는 OpenAPI를 정규 경로로 사용한다.

   서비스키: https://www.data.go.kr/data/15097584/openapi.do 활용신청(무료) 후 발급.
   GitHub Actions에서는 저장소 Secret  DATA_GO_KR_KEY  로 주입한다.
"""
import json, os, sys, time, pathlib, urllib.parse, urllib.request
HERE=pathlib.Path(__file__).resolve().parent; ROOT=HERE.parent
sys.path.insert(0, str(HERE))
from model import analyze
DATA=ROOT/"data"; RB=DATA/"recon-browser"
# 기획재정부_국고보조금 정보 (여러 오퍼레이션 중 보조사업 목록/집행)
ENDPOINTS=[
  "https://apis.data.go.kr/1051000/MoefOpenAPI/T_OPD_PRMSCT_SBBGST",  # 보조사업 현황
]
FIELD={"project":["bsnsNm","사업명","보조사업명","dtlBsnsNm","sbsdBsnsNm"],
 "recipient":["excInsttNm","보조사업자","수행기관","insttNm"],
 "grantor":["jrsdNm","소관","중앙관서","교부기관"],
 "grant":["교부액","보조금액","txamt","sumAmt","aftrKeepAmt","totlAmt","bsnsAmt"],
 "date":["dcsnDe","공시일","기준일"], "note":["bsnsId","dtlbzId","id"]}
def nk(s): 
    import re; return re.sub(r"[^0-9a-z가-힣]","",str(s or "").lower())
def to_num(v):
    import re; m=re.sub(r"[^0-9.\-]","",str(v or ""))
    try: return float(m) if m not in("","-",".") else 0.0
    except: return 0.0
def get(url):
    req=urllib.request.Request(url, headers={"User-Agent":"SubsidyResearch/1.0"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read().decode("utf-8","replace")
def xml_items(text):
    import xml.etree.ElementTree as ET
    try: root=ET.fromstring(text)
    except Exception: return None
    # 에러 헤더 확인
    rc=root.findtext(".//resultCode") or root.findtext(".//resultCode")
    items=root.findall(".//item")
    if not items: return None if (rc and rc not in ("0","00")) else []
    out=[]
    for it in items:
        out.append({c.tag: (c.text or "") for c in it})
    return out
def find_items(o,d=0):
    if d>6: return None
    if isinstance(o,list) and o and isinstance(o[0],dict): return o
    if isinstance(o,dict):
        for v in o.values():
            r=find_items(v,d+1)
            if r: return r
    return None
def to_rows(items):
    keys=list(items[0].keys()); m={}
    for f,al in FIELD.items():
        k=next((k for k in keys if nk(k) in {nk(a) for a in al}),None)
        if not k: k=next((k for k in keys if any(nk(a) in nk(k) for a in al)),None)
        if k: m[f]=k
    out=[]
    for it in items:
        rec={f:(to_num(it.get(m[f])) if f=="grant" else str(it.get(m[f],"")).strip()) for f in m}
        rec.setdefault("project",""); rec.setdefault("recipient","")
        rec["note"]="공시ID:"+rec.get("note","")
        if rec.get("project") or rec.get("recipient"): out.append(rec)
    return out, m, keys
def run():
    key=os.environ.get("DATA_GO_KR_KEY","").strip()
    RB.mkdir(parents=True,exist_ok=True); DATA.mkdir(exist_ok=True)
    if not key:
        print("DATA_GO_KR_KEY 미설정 — https://www.data.go.kr/data/15097584/openapi.do 에서 발급 후\n"
              "저장소 Settings→Secrets→Actions 에 DATA_GO_KR_KEY 로 추가하세요.")
        (RB/"openapi-debug.json").write_text(json.dumps({"error":"no key"},ensure_ascii=False),encoding="utf-8")
        return
    all_rows=[]; dbg=[]
    # 파라미터 조합 자동 탐색(어떤 조합이 데이터를 주는지)
    probe=[]
    for ep in ENDPOINTS:
        for variant in [
            {"resultType":"json"}, {"type":"json"}, {"dataType":"json"},
            {"_type":"json"}, {},  # 기본(XML)
        ]:
            q=dict(serviceKey=key, pageNo=1, numOfRows=10, **variant)
            url=f"{ep}?"+urllib.parse.urlencode(q, safe="%")
            try: body=get(url)
            except Exception as e: probe.append({"variant":variant,"error":str(e)}); continue
            items=None
            try: items=find_items(json.loads(body))
            except Exception: items=xml_items(body)
            probe.append({"variant":variant,"len":len(body),"items":(len(items) if items else 0),"head":body[:160]})
            if items:
                probe.append({"WORKING":variant}); break
    (RB/"openapi-probe.json").write_text(json.dumps(probe,ensure_ascii=False,indent=1),encoding="utf-8")
    working=next((p["WORKING"] for p in probe if p.get("WORKING")), {"resultType":"json"})
    print("작동 조합:", working)
    for ep in ENDPOINTS:
        page=1
        while page<=int(os.environ.get("MAX_PAGES","30")):
            qs=urllib.parse.urlencode(dict(serviceKey=key,pageNo=page,numOfRows=500,**working), safe="%")
            url=f"{ep}?{qs}"
            try: body=get(url)
            except Exception as e: dbg.append({"ep":ep,"page":page,"error":str(e)}); break
            if page==1: (RB/"openapi-first.json").write_text(body[:200000],encoding="utf-8")
            items=None
            try:
                js=json.loads(body); items=find_items(js)
            except Exception:
                items=xml_items(body)   # XML 응답 파싱
                if items is None:
                    dbg.append({"ep":ep,"page":page,"note":"파싱 실패","head":body[:200]}); break
            if not items: dbg.append({"ep":ep,"page":page,"note":"항목 없음","keys":list(js.keys())[:12] if isinstance(js,dict) else str(type(js))}); break
            rows,m,keys=to_rows(items)
            if page==1: dbg.append({"ep":ep,"itemKeys":keys[:30],"mappedTo":m})
            all_rows.extend(rows)
            print(f"{ep.split('/')[-1]} p{page}: {len(rows)}행 (누적 {len(all_rows)})")
            if len(items)<500: break
            page+=1; time.sleep(0.3)
    (RB/"openapi-debug.json").write_text(json.dumps(dbg,ensure_ascii=False,indent=1),encoding="utf-8")
    if all_rows:
        (DATA/"disclosures.json").write_text(json.dumps({"collectedAt":time.strftime("%Y-%m-%dT%H:%M:%SZ",time.gmtime()),
            "source":"data.go.kr OpenAPI","rowCount":len(all_rows),"rows":all_rows},ensure_ascii=False),encoding="utf-8")
        ranked=analyze(all_rows)
        (DATA/"analysis.json").write_text(json.dumps({"analyzedAt":time.strftime("%Y-%m-%dT%H:%M:%SZ",time.gmtime()),
            "projectCount":len(ranked),"projects":ranked},ensure_ascii=False),encoding="utf-8")
        print(f"완료: {len(all_rows)}행 / 사업 {len(ranked)}")
    else:
        print("데이터 없음 — data/recon-browser/openapi-debug.json 확인(키 승인 상태·엔드포인트)")
if __name__=="__main__": run()
