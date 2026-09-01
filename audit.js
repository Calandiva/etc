/* ═══════════════════════════════════════════════════════════════════
   정산 내역 정량 분석 엔진
   보조사업별 집행 내역(rows)을 받아 15개 정량 룰로 부정 징후를 계산.
   각 룰은 회계부정 적발에 실증적으로 쓰이는 지표(벤포드, 임계값 회피,
   거래 집중도, 라운드넘버 편중, 기간 쏠림 등)에 기반.
   ═══════════════════════════════════════════════════════════════════ */
const AUDIT_RULES = [
  {id:"split",   name:"쪼개기 계약 (수의계약 한도 회피)", w:10,
   why:"수의계약 한도(2천만원) 직전 금액이 같은 거래처에 반복되면 계약 분할 의심",
   ask:["계약서·발주 내역 일체","동일 거래처 계약 목록","입찰 면제 사유서"]},
  {id:"concen",  name:"특정 업체 몰아주기 (거래 집중)", w:9,
   why:"상위 1개 거래처 점유율과 허핀달지수(HHI)가 높으면 경쟁 배제·유착 의심",
   ask:["거래처 선정 사유·견적 비교표","계약 상대자 등록 자료"]},
  {id:"nobid",   name:"수의계약 비율 과다", w:7,
   why:"경쟁입찰 없이 집행된 비중이 과도하면 특혜성 계약 가능성",
   ask:["계약방법별 집행 내역","입찰 공고 자료"]},
  {id:"benford", name:"벤포드 법칙 이탈 (금액 조작 지표)", w:8,
   why:"자연 발생 회계금액의 선두자리 분포에서 벗어나면 인위적 금액 생성 의심",
   ask:["전체 집행 원장","증빙 원본 대조"]},
  {id:"round",   name:"라운드 넘버 편중", w:7,
   why:"딱 떨어지는 금액(10만/100만 단위) 비중이 과도하면 실거래 아닌 추정 정산 의심",
   ask:["거래명세서·세금계산서 원본","단가 산출 근거"]},
  {id:"yearend", name:"사업 종료 직전 집행 쏠림", w:6,
   why:"예산 소진을 위한 몰아치기 집행은 목적 외 사용·허위 정산의 전형",
   ask:["월별 집행 계획 대비 실적","납품·검수 일자 증빙"]},
  {id:"dup",     name:"중복 결제 (동일 거래처·금액·일자)", w:9,
   why:"같은 거래가 두 번 이상 정산되면 이중청구",
   ask:["해당 거래 세금계산서·이체 내역"]},
  {id:"repeat",  name:"동일 금액 반복 집행", w:7,
   why:"실거래라면 드문 완전 동일 금액의 반복은 허위 증빙 재사용 신호",
   ask:["각 거래 개별 증빙","납품 실물 확인"]},
  {id:"related", name:"특수관계 의심 거래 (동일 사업자번호·대표자)", w:10,
   why:"보조사업자와 거래처의 사업자번호·대표자가 일치하면 자기거래",
   ask:["법인등기·주주명부","가족관계 확인 자료"]},
  {id:"weekend", name:"주말·공휴일 집행", w:5,
   why:"영업일이 아닌 날짜의 집행은 일자 소급 작성 의심",
   ask:["거래 시점 입증 자료","실제 납품·용역 수행 기록"]},
  {id:"exact",   name:"집행률 100% 정확 일치", w:6,
   why:"교부액과 집행액이 1원 오차 없이 일치하면 잔액 반납 회피용 정산 맞추기 의심",
   ask:["정산보고서·잔액 반납 내역","최종 지출 증빙"]},
  {id:"labor",   name:"인건비 비중 이상", w:8,
   why:"인건비 비중이 비정상적으로 높거나 1인당 금액이 이상치면 유령직원·페이백 의심",
   ask:["급여대장·4대보험 명부","근태기록"]},
  {id:"newvend", name:"단일 거래 전용 거래처", w:7,
   why:"해당 사업에서만 한 번 등장하는 고액 거래처는 페이퍼컴퍼니 가능성",
   ask:["사업자등록·폐업 조회","사업장 실체 확인"]},
  {id:"nodoc",   name:"증빙 유형 미기재·불명", w:6,
   why:"증빙 항목이 비어 있거나 간이 증빙이면 지출 사실 확인 불가",
   ask:["세금계산서·계산서 원본","계좌 이체 확인증"]},
  {id:"neg",     name:"음수·취소 금액 존재", w:8,
   why:"마이너스 집행이나 취소분은 세금계산서 취소 후 미환입의 흔적",
   ask:["전자세금계산서 발행·취소 내역","환입 처리 증빙"]},
];

const WD = ["일","월","화","수","목","금","토"];
const BENFORD = [.301,.176,.125,.097,.079,.067,.058,.051,.046];

function parseDate(s){
  if(!s) return null;
  const t = String(s).replace(/[^0-9]/g,"");
  if(t.length<8) return null;
  const d = new Date(+t.slice(0,4), +t.slice(4,6)-1, +t.slice(6,8));
  return isNaN(d) ? null : d;
}
const pct = (a,b) => b ? a/b*100 : 0;

/** 집행 내역 배열 → 사업별 진단 결과 (부정 확률 내림차순) */
function auditProjects(rows, opts={}){
  const limit = opts.bidLimit ?? 20000000;      // 수의계약 한도(원)
  const groups = new Map();
  for(const r of rows){
    const key = `${r.project||"(사업명 미상)"}::${r.recipient||""}`;
    if(!groups.has(key)) groups.set(key,[]);
    groups.get(key).push(r);
  }
  const out = [];
  for(const [key,rs] of groups){
    const [project,recipient] = key.split("::");
    const amts = rs.map(r=>+r.amount||0);
    const total = amts.reduce((a,b)=>a+b,0);
    const pos = amts.filter(a=>a>0);
    if(!rs.length) continue;
    const fired = [];
    const add = (id,score,detail,evid) => fired.push({id,score:Math.min(100,Math.round(score)),detail,evid});

    /* 1. 쪼개기 계약 — 한도의 85~100% 구간 계약이 동일 거래처에 반복 */
    const nearLimit = rs.filter(r=>+r.amount>=limit*.85 && +r.amount<=limit);
    if(nearLimit.length>=2){
      const byV = {}; nearLimit.forEach(r=>byV[r.vendor||"(미상)"]=(byV[r.vendor||"(미상)"]||0)+1);
      const worst = Object.entries(byV).sort((a,b)=>b[1]-a[1])[0];
      if(worst[1]>=2) add("split", Math.min(100,40+worst[1]*15),
        `한도(${(limit/10000).toLocaleString()}만원)의 85~100% 구간 계약 ${nearLimit.length}건, 그중 '${worst[0]}' ${worst[1]}건`,
        nearLimit.slice(0,5));
    }
    /* 2. 거래 집중 — 상위 거래처 점유율 + HHI */
    const vend = {}; rs.forEach(r=>{ if(r.vendor) vend[r.vendor]=(vend[r.vendor]||0)+(+r.amount||0); });
    const vList = Object.entries(vend).sort((a,b)=>b[1]-a[1]);
    if(vList.length && total>0){
      const top = vList[0][1]/total*100;
      const hhi = vList.reduce((s,[,v])=>s+Math.pow(v/total*100,2),0);
      if(vList.length>=2 && top>=60) add("concen", Math.min(100,top),
        `상위 거래처 '${vList[0][0]}' 점유율 ${top.toFixed(1)}% (HHI ${Math.round(hhi)})`, []);
      else if(vList.length===1 && rs.length>=3) add("concen", 75,
        `전 거래가 단일 거래처 '${vList[0][0]}'에 집중`, []);
    }
    /* 3. 수의계약 비율 */
    const withM = rs.filter(r=>r.method);
    if(withM.length>=3){
      const noBid = withM.filter(r=>/수의|단독|1인|견적/.test(r.method)).length;
      const ratio = pct(noBid,withM.length);
      if(ratio>=70) add("nobid", ratio, `계약방법 기재 ${withM.length}건 중 수의계약 ${noBid}건(${ratio.toFixed(0)}%)`, []);
    }
    /* 4. 벤포드 선두자리 (MAD) */
    if(pos.length>=50){
      const cnt = Array(9).fill(0);
      pos.forEach(a=>{ const d=+String(Math.round(a)).replace(/^0+/,"")[0]; if(d>=1&&d<=9) cnt[d-1]++; });
      const n = cnt.reduce((a,b)=>a+b,0);
      const mad = cnt.reduce((s,c,i)=>s+Math.abs(c/n-BENFORD[i]),0)/9;
      // Nigrini 기준: <0.006 정상, 0.006~0.012 허용, 0.012~0.015 경계, >0.015 이탈
      // 표본이 작을수록 MAD 잡음이 크므로 신뢰도로 감쇠(200건에서 만점 반영)
      const bConf = Math.min(1, n/200);
      if(mad>=0.015) add("benford", Math.min(100, (30+(mad-0.015)*1800)*bConf),
        `선두자리 분포 MAD ${(mad*100).toFixed(2)}%p (0.6%p 이하 정상 / 1.5%p 초과 이탈) · 표본 ${n}건${bConf<1?" · 표본 부족으로 감쇠 적용":""}`, []);
    }
    /* 5. 라운드 넘버 편중 */
    if(pos.length>=10){
      const r6 = pos.filter(a=>a%1000000===0).length, r5 = pos.filter(a=>a%100000===0).length;
      const ratio = pct(r5,pos.length);
      if(ratio>=45) add("round", ratio,
        `10만원 단위 정액 ${r5}건(${ratio.toFixed(0)}%), 100만원 단위 ${r6}건 · 실거래에서는 드문 분포`,
        rs.filter(r=>+r.amount%1000000===0).slice(0,5));
    }
    /* 6. 종료 직전 쏠림 */
    const dated = rs.map(r=>({r,d:parseDate(r.date)})).filter(x=>x.d);
    if(dated.length>=8){
      const ds = dated.map(x=>x.d).sort((a,b)=>a-b);
      const span = (ds[ds.length-1]-ds[0])/86400000;
      if(span>=60){
        const cut = new Date(ds[ds.length-1].getTime()-span*.15*86400000);
        const late = dated.filter(x=>x.d>=cut);
        const amtLate = late.reduce((s,x)=>s+(+x.r.amount||0),0);
        const ratio = pct(amtLate,total);
        if(ratio>=45) add("yearend", ratio,
          `사업기간 마지막 15%(${cut.toISOString().slice(0,10)} 이후)에 금액의 ${ratio.toFixed(0)}% 집행`,
          late.slice(0,5).map(x=>x.r));
      }
    }
    /* 7. 중복 결제 */
    const seen = new Map(), dups = [];
    rs.forEach(r=>{ const k=`${r.vendor}|${r.amount}|${String(r.date).replace(/[^0-9]/g,"")}`;
      if(!r.vendor||!+r.amount||!r.date) return;
      if(seen.has(k)) dups.push(r); else seen.set(k,r); });
    if(dups.length) add("dup", Math.min(100,55+dups.length*12),
      `동일 거래처·금액·일자 중복 ${dups.length}건`, dups.slice(0,5));
    /* 8. 동일 금액 반복 */
    const byAmt = {}; pos.forEach(a=>byAmt[a]=(byAmt[a]||0)+1);
    const rep = Object.entries(byAmt).filter(([a,c])=>c>=4 && +a>=500000).sort((a,b)=>b[1]-a[1]);
    if(rep.length) add("repeat", Math.min(100,35+rep[0][1]*10),
      `${(+rep[0][0]).toLocaleString()}원이 ${rep[0][1]}회 반복(총 ${rep.length}종의 반복 금액)`,
      rs.filter(r=>+r.amount===+rep[0][0]).slice(0,5));
    /* 9. 특수관계 */
    const bn = (rs.find(r=>r.bizno)||{}).bizno, ceo = (rs.find(r=>r.ceo)||{}).ceo;
    const clean = s => String(s||"").replace(/[^0-9A-Za-z가-힣]/g,"");
    const rel = rs.filter(r =>
      (bn && r.vendorBizno && clean(r.vendorBizno)===clean(bn)) ||
      (ceo && r.vendor && clean(r.vendor).includes(clean(ceo)) && clean(ceo).length>=2));
    if(rel.length) add("related", Math.min(100,70+rel.length*8),
      `보조사업자와 사업자번호·대표자명이 겹치는 거래 ${rel.length}건`, rel.slice(0,5));
    /* 10. 주말·공휴일 */
    if(dated.length>=8){
      const we = dated.filter(x=>x.d.getDay()===0||x.d.getDay()===6);
      const ratio = pct(we.length,dated.length);
      if(ratio>=20) add("weekend", ratio*2,
        `주말 집행 ${we.length}건(${ratio.toFixed(0)}%)`,
        we.slice(0,5).map(x=>({...x.r, note:`${WD[x.d.getDay()]}요일`})));
    }
    /* 11. 집행률 정확 일치 */
    const grant = Math.max(...rs.map(r=>+r.grant||0));
    if(grant>0){
      const rate = total/grant*100;
      if(Math.abs(total-grant)<1 && rs.length>=5) add("exact", 70,
        `교부액과 집행액이 완전 일치(${grant.toLocaleString()}원) · 잔액 0원`, []);
      else if(rate>100.0001) add("exact", Math.min(100,60+(rate-100)*2),
        `집행액이 교부액 초과(집행률 ${rate.toFixed(1)}%)`, []);
    }
    /* 12. 인건비 비중·1인당 이상치 */
    const labor = rs.filter(r=>/인건비|급여|임금|보수|수당/.test(r.item||""));
    if(labor.length){
      const lAmt = labor.reduce((s,r)=>s+(+r.amount||0),0);
      const ratio = pct(lAmt,total);
      const heads = new Set(labor.map(r=>r.note).filter(Boolean)).size || labor.length;
      const per = lAmt/Math.max(1,heads);
      if(ratio>=70) add("labor", ratio, `인건비 비중 ${ratio.toFixed(0)}% (${labor.length}건)`, labor.slice(0,5));
      else if(per>=8000000) add("labor", Math.min(100,50+per/400000),
        `1인·1건당 인건비 평균 ${Math.round(per).toLocaleString()}원으로 과다`, labor.slice(0,5));
    }
    /* 13. 단일 거래 전용 고액 거래처 */
    const once = vList.filter(([v,amt])=>rs.filter(r=>r.vendor===v).length===1 && amt>=total*.25 && amt>=5000000);
    if(once.length) add("newvend", Math.min(100,45+once.length*15),
      `단 1회만 등장하는 고액 거래처 ${once.length}곳 (최대 '${once[0][0]}' ${once[0][1].toLocaleString()}원)`,
      rs.filter(r=>once.some(o=>o[0]===r.vendor)).slice(0,5));
    /* 14. 증빙 미기재 */
    const hasDocCol = rs.some(r=>r.doc);
    if(hasDocCol){
      const nod = rs.filter(r=>!r.doc || /간이|영수증|없음|미|기타/.test(r.doc));
      const ratio = pct(nod.length,rs.length);
      if(ratio>=30) add("nodoc", ratio*1.5,
        `증빙 미기재·간이증빙 ${nod.length}건(${ratio.toFixed(0)}%)`, nod.slice(0,5));
    }
    /* 15. 음수·취소 */
    const negs = rs.filter(r=>+r.amount<0);
    if(negs.length) add("neg", Math.min(100,50+negs.length*15),
      `음수(취소·환입) 집행 ${negs.length}건`, negs.slice(0,5));

    /* 종합 부정 확률 = 발동 룰의 가중 점수 / 전체 가중 상한, 표본 신뢰도 보정 */
    const maxW = AUDIT_RULES.reduce((s,r)=>s+r.w,0);
    const got = fired.reduce((s,f)=>s+(AUDIT_RULES.find(r=>r.id===f.id).w*f.score/100),0);
    const conf = Math.min(1, .55 + Math.log10(Math.max(1,rs.length))*0.25);  // 표본이 적으면 감쇠
    const prob = Math.min(99, Math.round(got/maxW*100*2.4*conf));
    out.push({project, recipient, rows:rs, n:rs.length, total, grant,
      vendors:vList.length, fired:fired.sort((a,b)=>b.score-a.score), prob, conf});
  }
  return out.sort((a,b)=>b.prob-a.prob || b.total-a.total);
}
