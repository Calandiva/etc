/* 부정수급 요건 모델 (브라우저판) — crawler/model.py 와 동일 로직.
   공시 집행내역 rows[] → 사업별 부정 가능성 %와 이유.
   요건 5축: A 허위청구 · B 과다청구 · C 목적 외 사용 · D 경쟁 회피·유착 · E 정산 신뢰성 훼손 */
(function (g) {
  const ELEMENTS = {
    A: { name: "허위청구", law: "보조금법 §40 · 공공재정환수법 §2① (제재부가금 500%)", w: 30 },
    B: { name: "과다청구", law: "공공재정환수법 §2② (제재부가금 300%)", w: 20 },
    C: { name: "목적 외 사용", law: "보조금법 §22·§41 · 공공재정환수법 §2③ (200%)", w: 15 },
    D: { name: "경쟁 회피·유착", law: "보조금법 시행령 계약규정 · 기재부 최다 적발 유형", w: 20 },
    E: { name: "정산 신뢰성 훼손", law: "보조금법 §27(정산보고)", w: 15 },
  };
  const BENFORD = [.301, .176, .125, .097, .079, .067, .058, .051, .046];
  const LIMIT = 20000000;
  const SEP = "";
  const num = v => { const n = parseFloat(String(v == null ? "" : v).replace(/[^0-9.\-]/g, "")); return isFinite(n) ? n : 0; };
  const dt = s => { const t = String(s == null ? "" : s).replace(/[^0-9]/g, ""); if (t.length < 8) return null; const d = new Date(+t.slice(0, 4), +t.slice(4, 6) - 1, +t.slice(6, 8)); return isNaN(d) ? null : d; };
  const won = v => { v = Math.round(+v || 0); const a = Math.abs(v); return a >= 1e8 ? (v / 1e8).toFixed(1).replace(/\.0$/, "") + "억원" : a >= 1e4 ? Math.round(v / 1e4).toLocaleString() + "만원" : v.toLocaleString() + "원"; };
  const clean = s => String(s == null ? "" : s).replace(/[^0-9A-Za-z가-힣]/g, "");

  function evalProject(rows) {
    const n = rows.length, amts = rows.map(r => num(r.amount)), pos = amts.filter(a => a > 0);
    const total = amts.reduce((a, b) => a + b, 0), grant = Math.max(0, ...rows.map(r => num(r.grant)));
    const vend = {}; rows.forEach(r => { if (r.vendor) vend[r.vendor] = (vend[r.vendor] || 0) + num(r.amount); });
    const vList = Object.entries(vend).sort((a, b) => b[1] - a[1]);
    const R = []; const hit = (e, s, text) => R.push({ elem: e, score: Math.min(100, Math.round(s)), text });

    // A 허위청구
    const bn = (rows.find(r => r.bizno) || {}).bizno, ceo = (rows.find(r => r.ceo) || {}).ceo;
    const rel = rows.filter(r => (bn && clean(r.vendorBizno) === clean(bn)) || (ceo && clean(ceo).length >= 2 && clean(r.vendor).includes(clean(ceo))));
    if (rel.length) hit("A", 70 + rel.length * 8, `보조사업자와 사업자번호·대표자명이 겹치는 자기거래 ${rel.length}건 (합계 ${won(rel.reduce((s, r) => s + num(r.amount), 0))}) — 허위계약 의심의 직접 신호`);
    const seen = new Set(), dups = []; rows.forEach(r => { const k = [r.vendor, num(r.amount), String(r.date).replace(/[^0-9]/g, "")].join("|"); if (r.vendor && num(r.amount) && r.date) { if (seen.has(k)) dups.push(r); seen.add(k); } });
    if (dups.length) hit("A", 55 + dups.length * 12, `동일 거래처·금액·일자의 중복 결제 ${dups.length}건 — 동일 거래의 이중청구 가능성`);
    const once = vList.filter(([v, a]) => rows.filter(r => r.vendor === v).length === 1 && a >= Math.max(5e6, total * .25));
    if (once.length) hit("A", 45 + once.length * 15, `단 1회만 등장하는 고액 거래처 ${once.length}곳 (최대 '${once[0][0]}' ${won(once[0][1])}) — 실체 없는 업체(유령회사) 확인 필요`);
    const dated = rows.map(r => [dt(r.date), r]).filter(x => x[0]);
    if (n >= 8 && dated.length) { const we = dated.filter(x => x[0].getDay() === 0 || x[0].getDay() === 6); if (we.length / dated.length >= .2) hit("A", we.length / dated.length * 200, `주말·공휴일 집행 ${we.length}건(${Math.round(we.length / dated.length * 100)}%) — 지출 일자 소급 작성 의심`); }
    if (pos.length >= 50) { const c = Array(9).fill(0); pos.forEach(a => { const d = +String(Math.round(a)).replace(/^0+/, "")[0]; if (d >= 1 && d <= 9) c[d - 1]++; }); const s = c.reduce((a, b) => a + b, 0), mad = c.reduce((x, ci, i) => x + Math.abs(ci / s - BENFORD[i]), 0) / 9, cf = Math.min(1, s / 200); if (mad >= .015) hit("A", (30 + (mad - .015) * 1800) * cf, `집행금액 선두자리 분포가 벤포드 법칙에서 이탈(MAD ${(mad * 100).toFixed(2)}%p, 정상≤0.6%p) — 인위적 금액일 때 나타나는 패턴 (표본 ${s}건)`); }
    // B 과다청구
    if (pos.length >= 10) { const r5 = pos.filter(a => a % 1e5 === 0).length; if (r5 / pos.length >= .45) hit("B", r5 / pos.length * 100, `10만원 단위 정액 거래가 ${r5}건(${Math.round(r5 / pos.length * 100)}%) — 실거래 아닌 맞춰넣은 단가·수량일 때 흔한 분포`); }
    const rep = {}; pos.forEach(a => { if (a >= 5e5) rep[a] = (rep[a] || 0) + 1; });
    const tr = Object.entries(rep).sort((a, b) => b[1] - a[1])[0];
    if (tr && tr[1] >= 4) hit("B", 35 + tr[1] * 10, `${won(+tr[0])}이 ${tr[1]}회 반복 집행 — 동일 증빙 재사용·기계적 분할 청구 의심`);
    if (grant > 0 && total > grant * 1.0001) hit("B", 60 + (total / grant - 1) * 200, `집행액(${won(total)})이 교부액(${won(grant)})을 초과(집행률 ${(total / grant * 100).toFixed(1)}%)`);
    // C 목적 외
    const labor = rows.filter(r => /인건비|급여|임금|보수|수당/.test(r.item || ""));
    if (labor.length) { const la = labor.reduce((s, r) => s + num(r.amount), 0); if (total > 0 && la / total >= .7) hit("C", la / total * 100, `인건비가 집행액의 ${Math.round(la / total * 100)}%(${won(la)}) — 유령직원·페이백 점검 필요 비중`); const per = la / Math.max(1, labor.length); if (per >= 8e6) hit("C", 50 + per / 4e5, `인건비 건당 평균 ${won(per)} — 통상 급여 대비 과다, 실근무 확인 필요`); }
    const ds = dated.map(x => x[0]).sort((a, b) => a - b);
    if (ds.length >= 8) { const span = (ds[ds.length - 1] - ds[0]) / 864e5; if (span >= 60) { const cut = ds[ds.length - 1].getTime() - span * .15 * 864e5; const la = rows.filter(r => { const d = dt(r.date); return d && d.getTime() >= cut; }).reduce((s, r) => s + num(r.amount), 0); if (total > 0 && la / total >= .45) hit("C", la / total * 100, `사업기간 마지막 15% 구간에 금액의 ${Math.round(la / total * 100)}% 집행 — 예산 소진성 몰아쓰기(목적 외 전용의 전형적 시점)`); } }
    // D 경쟁 회피·유착
    const near = rows.filter(r => num(r.amount) >= LIMIT * .85 && num(r.amount) <= LIMIT);
    if (near.length >= 2) { const bv = {}; near.forEach(r => bv[r.vendor || "(미상)"] = (bv[r.vendor || "(미상)"] || 0) + 1); const w = Object.entries(bv).sort((a, b) => b[1] - a[1])[0]; if (w[1] >= 2) hit("D", 40 + w[1] * 15, `수의계약 한도(${won(LIMIT)}) 바로 아래 금액의 계약 ${near.length}건, 그중 '${w[0]}'에 ${w[1]}건 — 한도 회피용 계약 쪼개기 패턴`); }
    if (total > 0 && vList.length) { const tv = vList[0][0], ta = vList[0][1], share = ta / total; if (vList.length >= 2 && share >= .6) { const hhi = vList.reduce((s, x) => s + Math.pow(x[1] / total * 100, 2), 0); hit("D", share * 100, `거래액의 ${Math.round(share * 100)}%가 '${tv}' 한 곳에 집중(HHI ${Math.round(hhi)}) — 몰아주기·유착 확인 필요`); } else if (vList.length === 1 && n >= 3) hit("D", 75, `전체 거래가 단일 거래처 '${tv}'에 집중`); }
    const meth = rows.map(r => r.method).filter(Boolean);
    if (meth.length >= 3) { const nb = meth.filter(m => /수의|단독|1인|견적/.test(m)).length; if (nb / meth.length >= .7) hit("D", nb / meth.length * 100, `계약방법 기재 ${meth.length}건 중 수의계약 ${nb}건(${Math.round(nb / meth.length * 100)}%) — 경쟁입찰 회피`); }
    // E 정산 신뢰성
    const negs = rows.filter(r => num(r.amount) < 0);
    if (negs.length) hit("E", 50 + negs.length * 15, `음수(취소·환입) 집행 ${negs.length}건 — 세금계산서 취소 후 대금 미환입 여부 확인 필요`);
    if (rows.some(r => r.doc)) { const nod = rows.filter(r => !r.doc || /간이|영수증|없음|^미|기타/.test(r.doc)); if (n && nod.length / n >= .3) hit("E", nod.length / n * 150, `증빙 미기재·간이증빙 ${nod.length}건(${Math.round(nod.length / n * 100)}%) — 지출 사실 검증 불가 구간`); }
    if (grant > 0 && Math.abs(total - grant) < 1 && n >= 5) hit("E", 70, `집행액과 교부액이 1원 오차 없이 일치(${won(grant)}) — 잔액 반납 회피용 정산 맞추기 의심`);

    const es = {};
    for (const e in ELEMENTS) { const sig = R.filter(r => r.elem === e); if (!sig.length) { es[e] = 0; continue; } let p = 1; sig.map(x => x.score / 100).sort((a, b) => b - a).slice(0, 3).forEach(s => p *= (1 - s * .85)); es[e] = Math.round((1 - p) * 100); }
    const raw = Object.keys(ELEMENTS).reduce((s, e) => s + ELEMENTS[e].w * es[e] / 100, 0);
    const cf = Math.min(1, .55 + Math.log10(Math.max(1, n)) * .25);
    const prob = Math.min(99, Math.round(raw * 1.35 * cf));
    R.sort((a, b) => b.score - a.score);
    return { prob, conf: +cf.toFixed(2), elements: es, n, total: Math.round(total), grant: Math.round(grant), vendors: vList.length, reasons: R };
  }

  function analyze(rows) {
    const grp = {};
    rows.forEach(r => { const k = (r.project || "(사업명 미상)") + SEP + (r.recipient || ""); (grp[k] = grp[k] || []).push(r); });
    const out = Object.keys(grp).map(k => { const parts = k.split(SEP); return Object.assign(evalProject(grp[k]), { project: parts[0], recipient: parts[1], grantor: (grp[k].find(r => r.grantor) || {}).grantor || "" }); });
    out.sort((a, b) => b.prob - a.prob || b.total - a.total);
    return out;
  }
  g.FRAUD_MODEL = { ELEMENTS, analyze, won };
})(typeof window !== "undefined" ? window : globalThis);
