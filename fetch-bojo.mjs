#!/usr/bin/env node
/**
 * fetch-bojo.mjs — 보조사업 정산·집행 공시 데이터 수집기
 *
 * 이 스크립트는 정부 공개 API/공시 파일에서 보조사업 정산 내역을 받아
 * 분석 엔진이 읽는 표준 JSON(rows[])으로 정규화합니다.
 * 인터넷이 되는 회원님 PC에서 실행하세요.
 *
 * 사용법
 *   1) 공공데이터포털 OpenAPI (인증키 필요: https://www.data.go.kr/data/15097584/openapi.do)
 *      node fetch-bojo.mjs api --key=<serviceKey> --pages=20 --out=data.json
 *   2) 포털에서 내려받은 CSV/TSV/XLSX 변환
 *      node fetch-bojo.mjs file 집행현황.csv --out=data.json
 *   3) 여러 파일 병합
 *      node fetch-bojo.mjs file a.csv b.csv --out=data.json
 *
 * 출력 스키마 (분석 엔진 입력)
 *   { rows: [{ project, grantor, recipient, bizno, ceo, item, vendor,
 *              vendorBizno, method, amount, date, doc, note }] }
 */
import fs from "node:fs";
import path from "node:path";

const ENDPOINTS = {
  // 기획재정부_국고보조금 정보
  moef: "http://apis.data.go.kr/1051000/MoefOpenAPI/T_OPD_PRMSCT_SBBGST",
};

/* ── 컬럼 자동 매핑: 공시 파일마다 헤더가 달라 별칭을 폭넓게 인식 ── */
const ALIAS = {
  project:    ["보조사업명","사업명","세부사업명","내역사업명","사업","과제명"],
  grantor:    ["교부기관","소관기관","중앙관서","지자체","교부처","소관부처"],
  recipient:  ["보조사업자","보조사업자명","수급자","수행기관","단체명","업체명","기관명"],
  bizno:      ["사업자등록번호","보조사업자사업자번호","사업자번호"],
  ceo:        ["대표자","대표자명","대표"],
  item:       ["비목","비목명","예산비목","계정과목","지출항목","세목"],
  vendor:     ["거래처","거래처명","지급처","수취인","계약상대자","업체","공급자"],
  vendorBizno:["거래처사업자번호","공급자사업자번호","계약상대자사업자번호"],
  method:     ["계약방법","계약방식","구매방법","계약구분"],
  amount:     ["집행액","지출액","금액","계약금액","지급액","집행금액","결제금액"],
  grant:      ["교부액","교부금액","보조금액","지원금액","예산액"],
  date:       ["집행일자","지출일자","거래일자","계약일자","지급일","일자","결제일"],
  doc:        ["증빙","증빙유형","증빙서류","세금계산서번호","증빙번호"],
  note:       ["비고","적요","내용","산출내역"],
};
const norm = s => String(s ?? "").replace(/[\s "']/g, "").toLowerCase();
function buildMap(headers) {
  const map = {};
  for (const [key, names] of Object.entries(ALIAS)) {
    const i = headers.findIndex(h => names.some(n => norm(h) === norm(n)));
    const j = i >= 0 ? i : headers.findIndex(h => names.some(n => norm(h).includes(norm(n))));
    if (j >= 0) map[key] = j;
  }
  return map;
}
const toNum = v => {
  const n = Number(String(v ?? "").replace(/[^0-9.-]/g, ""));
  return Number.isFinite(n) ? n : 0;
};

/* ── CSV/TSV 파서 (따옴표·개행 포함 필드 지원) ── */
function parseDelimited(text) {
  text = text.replace(/^﻿/, "");
  const delim = (text.split("\n")[0].match(/\t/g) || []).length >
                (text.split("\n")[0].match(/,/g) || []).length ? "\t" : ",";
  const rows = []; let row = [], cell = "", q = false;
  for (let i = 0; i < text.length; i++) {
    const c = text[i];
    if (q) {
      if (c === '"') { if (text[i + 1] === '"') { cell += '"'; i++; } else q = false; }
      else cell += c;
    } else if (c === '"') q = true;
    else if (c === delim) { row.push(cell); cell = ""; }
    else if (c === "\n") { row.push(cell); rows.push(row); row = []; cell = ""; }
    else if (c !== "\r") cell += c;
  }
  if (cell || row.length) { row.push(cell); rows.push(row); }
  return rows.filter(r => r.some(c => String(c).trim() !== ""));
}

/* ── XLSX: 의존성 없이 sheet1의 inline/shared string + 숫자만 추출 ── */
async function parseXlsx(buf) {
  const { execSync } = await import("node:child_process");
  const tmp = fs.mkdtempSync("/tmp/xlsx-");
  const f = path.join(tmp, "b.xlsx");
  fs.writeFileSync(f, buf);
  try { execSync(`cd ${tmp} && unzip -qo b.xlsx`); }
  catch { throw new Error("XLSX 해제 실패: unzip이 필요합니다. CSV로 저장 후 다시 시도하세요."); }
  const shared = [];
  const ssPath = path.join(tmp, "xl/sharedStrings.xml");
  if (fs.existsSync(ssPath))
    for (const m of fs.readFileSync(ssPath, "utf8").matchAll(/<si>([\s\S]*?)<\/si>/g))
      shared.push([...m[1].matchAll(/<t[^>]*>([\s\S]*?)<\/t>/g)].map(x => x[1]).join(""));
  const sheet = fs.readFileSync(path.join(tmp, "xl/worksheets/sheet1.xml"), "utf8");
  const out = [];
  for (const rowM of sheet.matchAll(/<row[^>]*>([\s\S]*?)<\/row>/g)) {
    const cells = [];
    for (const cM of rowM[1].matchAll(/<c r="([A-Z]+)\d+"(?:[^>]*t="([^"]*)")?[^>]*>([\s\S]*?)<\/c>/g)) {
      let col = 0; for (const ch of cM[1]) col = col * 26 + (ch.charCodeAt(0) - 64);
      const v = (cM[3].match(/<v>([\s\S]*?)<\/v>/) || [])[1] ?? "";
      const inline = [...cM[3].matchAll(/<t[^>]*>([\s\S]*?)<\/t>/g)].map(x => x[1]).join("");
      cells[col - 1] = cM[2] === "s" ? (shared[+v] ?? "") : (inline || v);
    }
    out.push([...cells].map(c => c ?? ""));
  }
  fs.rmSync(tmp, { recursive: true, force: true });
  return out.filter(r => r.some(c => String(c).trim() !== ""));
}

function tableToRows(table, source) {
  if (!table.length) return [];
  // 헤더 행 탐색: 별칭이 가장 많이 매칭되는 상위 5행 중 하나
  let hi = 0, best = -1;
  for (let i = 0; i < Math.min(5, table.length); i++) {
    const n = Object.keys(buildMap(table[i])).length;
    if (n > best) { best = n; hi = i; }
  }
  const map = buildMap(table[hi]);
  if (best < 3) throw new Error(`컬럼을 인식하지 못했습니다(${source}). 헤더 예: 보조사업명, 보조사업자, 거래처, 계약방법, 집행액, 집행일자`);
  const pick = (r, k) => (map[k] != null ? String(r[map[k]] ?? "").trim() : "");
  return table.slice(hi + 1).map(r => ({
    project: pick(r, "project"), grantor: pick(r, "grantor"),
    recipient: pick(r, "recipient"), bizno: pick(r, "bizno"), ceo: pick(r, "ceo"),
    item: pick(r, "item"), vendor: pick(r, "vendor"), vendorBizno: pick(r, "vendorBizno"),
    method: pick(r, "method"), amount: toNum(pick(r, "amount")),
    grant: toNum(pick(r, "grant")), date: pick(r, "date"),
    doc: pick(r, "doc"), note: pick(r, "note"), source,
  })).filter(r => r.project || r.recipient || r.amount);
}

async function fromFiles(files) {
  const rows = [];
  for (const f of files) {
    const buf = fs.readFileSync(f);
    const table = /\.xlsx?$/i.test(f) ? await parseXlsx(buf) : parseDelimited(buf.toString("utf8"));
    const got = tableToRows(table, path.basename(f));
    console.log(`  ${path.basename(f)}: ${got.length}행`);
    rows.push(...got);
  }
  return rows;
}

async function fromApi({ key, pages, perPage, endpoint }) {
  const rows = [];
  for (let p = 1; p <= pages; p++) {
    const url = `${endpoint}?serviceKey=${encodeURIComponent(key)}&pageNo=${p}&numOfRows=${perPage}&type=json`;
    const res = await fetch(url);
    if (!res.ok) throw new Error(`API ${res.status} ${res.statusText} (인증키·승인상태를 확인하세요)`);
    const text = await res.text();
    let items;
    try {
      const j = JSON.parse(text);
      items = j?.response?.body?.items?.item ?? j?.response?.body?.items ?? j?.items ?? j?.data ?? [];
    } catch { throw new Error(`JSON 응답이 아닙니다. 앞부분: ${text.slice(0, 200)}`); }
    if (!Array.isArray(items) || !items.length) { console.log(`  page ${p}: 응답 없음 — 중단`); break; }
    const headers = Object.keys(items[0]);
    const table = [headers, ...items.map(o => headers.map(h => o[h]))];
    const got = tableToRows(table, "data.go.kr");
    rows.push(...got);
    console.log(`  page ${p}: ${got.length}행 (누적 ${rows.length})`);
    await new Promise(r => setTimeout(r, 120));
  }
  return rows;
}

/* ── main ── */
const argv = process.argv.slice(2);
const mode = argv[0];
const opt = Object.fromEntries(argv.filter(a => a.startsWith("--"))
  .map(a => { const [k, ...v] = a.slice(2).split("="); return [k, v.join("=") || true]; }));
const out = opt.out || "data.json";

try {
  let rows = [];
  if (mode === "api") {
    if (!opt.key) throw new Error("--key=<serviceKey> 가 필요합니다. https://www.data.go.kr/data/15097584/openapi.do 에서 활용신청 후 발급받으세요.");
    console.log("공공데이터포털에서 수집 중…");
    rows = await fromApi({ key: opt.key, pages: +opt.pages || 10, perPage: +opt.rows || 500,
                           endpoint: opt.endpoint || ENDPOINTS.moef });
  } else if (mode === "file") {
    const files = argv.slice(1).filter(a => !a.startsWith("--"));
    if (!files.length) throw new Error("변환할 CSV/TSV/XLSX 파일을 지정하세요.");
    console.log("파일 변환 중…");
    rows = await fromFiles(files);
  } else {
    console.log(`보조사업 정산 공시 데이터 수집기

  node fetch-bojo.mjs api --key=<serviceKey> [--pages=20] [--rows=500] [--out=data.json]
  node fetch-bojo.mjs file <파일...> [--out=data.json]

수집처
  · 공공데이터포털 기획재정부_국고보조금 정보   https://www.data.go.kr/data/15097584/openapi.do
  · 공공데이터포털 국고보조금 집행/보조사업 현황 https://www.data.go.kr/data/15126793/openapi.do
  · 보조금통합포털 집행현황·보조사업자 현황     https://bojo.go.kr/opn/ig/ig002/getIG002002QView.do
  · e나라도움 통계센터(엑셀·CSV 다운로드)      https://opn.gosims.go.kr/opn/iz/iz000/getIZ000002QView.do
  · 보조사업자 정보공시(정산보고서 원문)        https://www.bojo.go.kr/ea/getEA001101View.do

생성된 data.json 을 '보조금 부정수급 조사 데스크'의 [정산 데이터 분석] 탭에 올리면
쪼개기 계약·몰아주기·벤포드 편차 등 정량 룰로 부정 확률 순위가 산출됩니다.`);
    process.exit(0);
  }
  if (!rows.length) throw new Error("수집된 행이 없습니다.");
  fs.writeFileSync(out, JSON.stringify({ fetchedAt: new Date().toISOString(), rows }, null, 0));
  const projects = new Set(rows.map(r => r.project || r.recipient)).size;
  console.log(`\n완료: ${rows.length}행 / 사업 ${projects}건 → ${out}`);
  console.log(`다음: 조사 데스크 [정산 데이터 분석] 탭에서 ${out} 업로드`);
} catch (e) {
  console.error("오류:", e.message);
  process.exit(1);
}
