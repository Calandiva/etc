"""부정수급 요건 모델.

보조금법·공공재정환수법의 법적 구성요건 5개 축으로 공시 집행내역의 정량 신호를 묶고,
사업별 부정 가능성(%)과 사람이 읽을 수 있는 '이유' 목록을 산출한다.

  A. 허위청구   — 거짓 신청·거짓 증빙으로 청구 (보조금법 §40, 공공재정환수법 §2 ①, 제재부가금 500%)
  B. 과다청구   — 정당 금액을 초과한 청구 (공공재정환수법 §2 ②, 300%)
  C. 목적 외 사용 — 교부 목적과 다른 용도 집행 (보조금법 §22·§41, 공공재정환수법 §2 ③, 200%)
  D. 경쟁 회피·유착 — 쪼개기·수의계약 남용·특정업체 몰아주기 (보조금법 시행령 계약 규정, 기재부 최다 적발 유형)
  E. 정산 신뢰성 훼손 — 증빙 미비·취소·잔액 맞추기 (보조금법 §27 정산보고)

각 신호는 회계부정 적발에서 실증적으로 쓰이는 지표다(벤포드 MAD, 임계값 회피 클러스터,
거래 집중도 HHI, 라운드넘버 편중, 기간 쏠림, 완전 중복 등).
출력의 %는 '공시 내역의 통계적 이상 징후 수준'이며 부정수급의 확정이 아니다.
"""
from __future__ import annotations

import math
import re
from collections import Counter, defaultdict
from datetime import datetime

BENFORD = [0.301, 0.176, 0.125, 0.097, 0.079, 0.067, 0.058, 0.051, 0.046]
NOBID_LIMIT = 20_000_000        # 수의계약 한도(원) — 일반 용역·물품 기준
WEEKDAY = ["월", "화", "수", "목", "금", "토", "일"]

# (요건축, 신호ID, 가중치) — 가중치는 정부 적발 유형 비중(특정거래 38.5%, 급여성 22.7%,
# 가족간 17.2%, 증빙미비 12.2%)과 수법의 법적 명백성을 반영해 배분
ELEMENTS = {
    "A": {"name": "허위청구", "law": "보조금법 §40 · 공공재정환수법 §2①(제재부가금 500%)", "w": 30},
    "B": {"name": "과다청구", "law": "공공재정환수법 §2②(제재부가금 300%)", "w": 20},
    "C": {"name": "목적 외 사용", "law": "보조금법 §22·§41 · 공공재정환수법 §2③(200%)", "w": 15},
    "D": {"name": "경쟁 회피·유착", "law": "보조금법 시행령 계약규정 · 기재부 최다 적발 유형", "w": 20},
    "E": {"name": "정산 신뢰성 훼손", "law": "보조금법 §27(정산보고) · 증빙 규정", "w": 15},
}


def _num(v):
    try:
        return float(re.sub(r"[^0-9.\-]", "", str(v or "")) or 0)
    except ValueError:
        return 0.0


def _date(s):
    t = re.sub(r"[^0-9]", "", str(s or ""))
    if len(t) < 8:
        return None
    try:
        return datetime(int(t[:4]), int(t[4:6]), int(t[6:8]))
    except ValueError:
        return None


def _won(v):
    v = int(v)
    if abs(v) >= 100_000_000:
        return f"{v/100_000_000:.1f}억원".replace(".0억", "억")
    if abs(v) >= 10_000:
        return f"{v//10_000:,}만원"
    return f"{v:,}원"


def evaluate_project(rows: list[dict]) -> dict:
    """한 보조사업의 집행 행들 → {prob, elements:{축:점수}, reasons:[{elem,signal,score,text}]}"""
    n = len(rows)
    amts = [_num(r.get("amount")) for r in rows]
    pos = [a for a in amts if a > 0]
    total = sum(amts)
    grant = max((_num(r.get("grant")) for r in rows), default=0)
    vendors = Counter()
    for r in rows:
        if r.get("vendor"):
            vendors[r["vendor"]] += _num(r.get("amount"))
    reasons = []

    def hit(elem, sid, score, text):
        reasons.append({"elem": elem, "signal": sid, "score": min(100, round(score)), "text": text})

    # ── A. 허위청구 신호 ──────────────────────────────────────────
    bizno = next((r.get("bizno") for r in rows if r.get("bizno")), "")
    ceo = next((r.get("ceo") for r in rows if r.get("ceo")), "")
    clean = lambda s: re.sub(r"[^0-9A-Za-z가-힣]", "", str(s or ""))
    rel = [r for r in rows if (bizno and clean(r.get("vendorBizno")) == clean(bizno))
           or (ceo and len(clean(ceo)) >= 2 and clean(ceo) in clean(r.get("vendor")))]
    if rel:
        hit("A", "self-deal", 70 + len(rel) * 8,
            f"보조사업자와 사업자번호·대표자명이 겹치는 자기거래 {len(rel)}건 "
            f"(합계 {_won(sum(_num(r.get('amount')) for r in rel))}) — 허위계약 의심의 직접 신호")
    key_seen, dups = set(), []
    for r in rows:
        k = (r.get("vendor"), _num(r.get("amount")), re.sub(r"[^0-9]", "", str(r.get("date") or "")))
        if all(k) and k in key_seen:
            dups.append(r)
        key_seen.add(k)
    if dups:
        hit("A", "duplicate", 55 + len(dups) * 12,
            f"동일 거래처·금액·일자의 중복 결제 {len(dups)}건 — 동일 거래의 이중청구 가능성")
    once_big = [(v, a) for v, a in vendors.items()
                if sum(1 for r in rows if r.get("vendor") == v) == 1 and a >= max(5_000_000, total * 0.25)]
    if once_big:
        v0, a0 = max(once_big, key=lambda x: x[1])
        hit("A", "one-shot-vendor", 45 + len(once_big) * 15,
            f"단 1회만 등장하는 고액 거래처 {len(once_big)}곳 (최대 '{v0}' {_won(a0)}) — 실체 없는 업체(유령회사) 확인 필요")
    if n >= 8:
        dated = [(_date(r.get("date")), r) for r in rows]
        dated = [(d, r) for d, r in dated if d]
        we = [(d, r) for d, r in dated if d.weekday() >= 5]
        if dated and len(we) / len(dated) >= 0.2:
            hit("A", "weekend", len(we) / len(dated) * 200,
                f"주말·공휴일 집행 {len(we)}건({len(we)/len(dated)*100:.0f}%) — 지출 일자 소급 작성 의심")
    if len(pos) >= 50:
        cnt = [0] * 9
        for a in pos:
            d = int(str(int(a)).lstrip("0")[:1] or 0)
            if 1 <= d <= 9:
                cnt[d - 1] += 1
        s = sum(cnt)
        mad = sum(abs(c / s - BENFORD[i]) for i, c in enumerate(cnt)) / 9
        conf = min(1.0, s / 200)
        if mad >= 0.015:
            hit("A", "benford", (30 + (mad - 0.015) * 1800) * conf,
                f"집행금액 선두자리 분포가 벤포드 법칙에서 이탈(MAD {mad*100:.2f}%p, 정상≤0.6%p) "
                f"— 인위적으로 만든 금액일 때 나타나는 패턴 (표본 {s}건)")

    # ── B. 과다청구 신호 ──────────────────────────────────────────
    if len(pos) >= 10:
        r5 = sum(1 for a in pos if a % 100_000 == 0)
        if r5 / len(pos) >= 0.45:
            hit("B", "round", r5 / len(pos) * 100,
                f"10만원 단위 정액 거래가 {r5}건({r5/len(pos)*100:.0f}%) — 실거래가 아니라 "
                f"맞춰 넣은 단가·수량일 때 흔한 분포")
    rep = Counter(a for a in pos if a >= 500_000)
    top_rep = rep.most_common(1)
    if top_rep and top_rep[0][1] >= 4:
        a0, c0 = top_rep[0]
        hit("B", "repeat-amount", 35 + c0 * 10,
            f"{_won(a0)}이 {c0}회 반복 집행 — 동일 증빙 재사용·기계적 분할 청구 의심")
    if grant > 0 and total > grant * 1.0001:
        hit("B", "over-grant", 60 + (total / grant - 1) * 200,
            f"집행액({_won(total)})이 교부액({_won(grant)})을 초과(집행률 {total/grant*100:.1f}%)")

    # ── C. 목적 외 사용 신호 ──────────────────────────────────────
    labor = [r for r in rows if re.search(r"인건비|급여|임금|보수|수당", str(r.get("item") or ""))]
    if labor:
        l_amt = sum(_num(r.get("amount")) for r in labor)
        if total > 0 and l_amt / total >= 0.7:
            hit("C", "labor-heavy", l_amt / total * 100,
                f"인건비가 집행액의 {l_amt/total*100:.0f}%({_won(l_amt)}) — 유령직원·페이백 점검 필요 비중")
        per = l_amt / max(1, len(labor))
        if per >= 8_000_000:
            hit("C", "labor-outlier", 50 + per / 400_000,
                f"인건비 건당 평균 {_won(per)} — 통상 급여 대비 과다, 실근무 확인 필요")
    dated = sorted(d for d in (_date(r.get("date")) for r in rows) if d)
    if len(dated) >= 8:
        span = (dated[-1] - dated[0]).days
        if span >= 60:
            cut = dated[-1].timestamp() - span * 0.15 * 86400
            late_amt = sum(_num(r.get("amount")) for r in rows
                           if (_d := _date(r.get("date"))) and _d.timestamp() >= cut)
            if total > 0 and late_amt / total >= 0.45:
                hit("C", "year-end", late_amt / total * 100,
                    f"사업기간 마지막 15% 구간에 금액의 {late_amt/total*100:.0f}% 집행 "
                    f"— 예산 소진성 몰아쓰기(목적 외 전용의 전형적 시점)")

    # ── D. 경쟁 회피·유착 신호 ────────────────────────────────────
    near = [r for r in rows if NOBID_LIMIT * 0.85 <= _num(r.get("amount")) <= NOBID_LIMIT]
    if len(near) >= 2:
        by_v = Counter(r.get("vendor") or "(미상)" for r in near)
        v0, c0 = by_v.most_common(1)[0]
        if c0 >= 2:
            hit("D", "split", 40 + c0 * 15,
                f"수의계약 한도({_won(NOBID_LIMIT)}) 바로 아래 금액의 계약 {len(near)}건, "
                f"그중 '{v0}'에 {c0}건 — 한도 회피용 계약 쪼개기 패턴")
    if total > 0 and len(vendors) >= 1:
        top_v, top_a = vendors.most_common(1)[0]
        share = top_a / total
        if len(vendors) >= 2 and share >= 0.6:
            hhi = sum((a / total) ** 2 for a in vendors.values()) * 10000
            hit("D", "concentration", share * 100,
                f"거래액의 {share*100:.0f}%가 '{top_v}' 한 곳에 집중(HHI {hhi:.0f}) — 몰아주기·유착 확인 필요")
        elif len(vendors) == 1 and n >= 3:
            hit("D", "single-vendor", 75, f"전체 거래가 단일 거래처 '{top_v}'에 집중")
    methods = [str(r.get("method") or "") for r in rows if r.get("method")]
    if len(methods) >= 3:
        nobid = sum(1 for m in methods if re.search(r"수의|단독|1인|견적", m))
        if nobid / len(methods) >= 0.7:
            hit("D", "no-bid", nobid / len(methods) * 100,
                f"계약방법 기재 {len(methods)}건 중 수의계약 {nobid}건({nobid/len(methods)*100:.0f}%) — 경쟁입찰 회피")

    # ── E. 정산 신뢰성 신호 ───────────────────────────────────────
    negs = [r for r in rows if _num(r.get("amount")) < 0]
    if negs:
        hit("E", "negative", 50 + len(negs) * 15,
            f"음수(취소·환입) 집행 {len(negs)}건 — 세금계산서 취소 후 대금 미환입 여부 확인 필요")
    if any(r.get("doc") for r in rows):
        nod = [r for r in rows if not r.get("doc") or re.search(r"간이|영수증|없음|^미|기타", str(r.get("doc")))]
        if n and len(nod) / n >= 0.3:
            hit("E", "no-doc", len(nod) / n * 150,
                f"증빙 미기재·간이증빙 {len(nod)}건({len(nod)/n*100:.0f}%) — 지출 사실 검증 불가 구간")
    if grant > 0 and abs(total - grant) < 1 and n >= 5:
        hit("E", "exact-100", 70,
            f"집행액과 교부액이 1원 오차 없이 일치({_won(grant)}) — 잔액 반납 회피용 정산 맞추기 의심")

    # ── 종합: 요건축별 점수 → 부정 가능성 % ────────────────────────
    elem_scores = {}
    for e in ELEMENTS:
        sig = [r for r in reasons if r["elem"] == e]
        if not sig:
            elem_scores[e] = 0
            continue
        # 같은 축에서 신호가 겹칠수록 확신 증가(단순 max가 아니라 보수적 결합)
        p = 1.0
        for s in sorted((x["score"] / 100 for x in sig), reverse=True)[:3]:
            p *= (1 - s * 0.85)
        elem_scores[e] = round((1 - p) * 100)
    raw = sum(ELEMENTS[e]["w"] * elem_scores[e] / 100 for e in ELEMENTS)  # 0~100
    conf = min(1.0, 0.55 + math.log10(max(1, n)) * 0.25)                  # 표본 신뢰도
    prob = min(99, round(raw * 1.35 * conf))
    reasons.sort(key=lambda r: -r["score"])
    return {"prob": prob, "conf": round(conf, 2), "elements": elem_scores,
            "n": n, "total": int(total), "grant": int(grant),
            "vendors": len(vendors), "reasons": reasons}


def analyze(rows: list[dict]) -> list[dict]:
    """전체 행 → 사업별 평가, 부정 가능성 내림차순."""
    groups = defaultdict(list)
    for r in rows:
        groups[(str(r.get("project") or "(사업명 미상)"), str(r.get("recipient") or ""))].append(r)
    out = []
    for (project, recipient), rs in groups.items():
        ev = evaluate_project(rs)
        ev.update({"project": project, "recipient": recipient,
                   "grantor": next((r.get("grantor") for r in rs if r.get("grantor")), ""),
                   "noteIds": sorted({r.get("note") for r in rs if str(r.get("note") or "").startswith("공시ID")})})
        out.append(ev)
    out.sort(key=lambda p: (-p["prob"], -p["total"]))
    return out
