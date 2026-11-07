import fs from "node:fs/promises";
import sharp from "sharp";
import { FileBlob, SpreadsheetFile } from "@oai/artifact-tool";

const inputPath = "/Users/shibicheng/Desktop/K线导出_600584_日线数据.xlsx";
const outputDir = "/Users/shibicheng/shilun_standalone/outputs/019f978a-chan-analysis";
const outputPath = `${outputDir}/长电科技_600584_日线缠论分析.xlsx`;
const svgPath = `${outputDir}/chan_chart.svg`;
const pngPath = `${outputDir}/chan_chart.png`;
const previewPath = `${outputDir}/final_preview.png`;

const input = await FileBlob.load(inputPath);
const workbook = await SpreadsheetFile.importXlsx(input);
const source = workbook.worksheets.getItemAt(0);
source.name = "原始数据";

const raw = source.getRange("A1:M83").values;
const rows = raw.slice(1).filter(r => Number.isFinite(r[2]) && Number.isFinite(r[3]) && Number.isFinite(r[4]) && Number.isFinite(r[5]) && Number.isFinite(r[6]));
const serialToDate = n => new Date(Date.UTC(1899, 11, 30) + n * 86400000);
const fmtDate = d => d.toISOString().slice(0, 10);
const bars = rows.map((r, i) => ({
  i, code: String(r[0]), name: String(r[1]), date: serialToDate(r[2]),
  open: +r[3], high: +r[4], low: +r[5], close: +r[6],
  pct: +r[8], volume: +r[9], amount: +r[10],
}));

function macdSeries(data) {
  let ema12 = data[0].close, ema26 = data[0].close, dea = 0;
  return data.map((b, i) => {
    if (i) {
      ema12 = ema12 * 11 / 13 + b.close * 2 / 13;
      ema26 = ema26 * 25 / 27 + b.close * 2 / 27;
    }
    const dif = ema12 - ema26;
    dea = i ? dea * 8 / 10 + dif * 2 / 10 : dif;
    return { ema12, ema26, dif, dea, hist: 2 * (dif - dea) };
  });
}
const macd = macdSeries(bars);

function compressInclusions(data) {
  const out = [];
  let dir = 0;
  for (const b of data) {
    const cur = { ...b, start: b.i, end: b.i, sourceCount: 1 };
    if (!out.length) { out.push(cur); continue; }
    const last = out[out.length - 1];
    const contained = (cur.high <= last.high && cur.low >= last.low) || (cur.high >= last.high && cur.low <= last.low);
    if (!contained) {
      if (cur.high > last.high && cur.low > last.low) dir = 1;
      else if (cur.high < last.high && cur.low < last.low) dir = -1;
      out.push(cur);
      continue;
    }
    if (!dir && out.length >= 2) {
      const prev = out[out.length - 2];
      dir = last.high >= prev.high ? 1 : -1;
    }
    if (!dir) dir = cur.close >= last.close ? 1 : -1;
    const merged = {
      ...last,
      end: cur.end,
      sourceCount: last.sourceCount + 1,
      close: cur.close,
      high: dir > 0 ? Math.max(last.high, cur.high) : Math.min(last.high, cur.high),
      low: dir > 0 ? Math.max(last.low, cur.low) : Math.min(last.low, cur.low),
    };
    merged.open = last.open;
    out[out.length - 1] = merged;
  }
  return out;
}
const compressed = compressInclusions(bars);

function detectFractals(data) {
  const out = [];
  for (let i = 1; i < data.length - 1; i++) {
    const a = data[i - 1], b = data[i], c = data[i + 1];
    if (b.high > a.high && b.high > c.high && b.low > a.low && b.low > c.low)
      out.push({ type: "顶", ci: i, oi: b.end, date: bars[b.end].date, price: b.high });
    if (b.high < a.high && b.high < c.high && b.low < a.low && b.low < c.low)
      out.push({ type: "底", ci: i, oi: b.end, date: bars[b.end].date, price: b.low });
  }
  return out;
}
const fractals = detectFractals(compressed);

function buildPens(fracs) {
  const selected = [];
  for (const f of fracs) {
    if (!selected.length) { selected.push(f); continue; }
    const last = selected[selected.length - 1];
    if (f.type === last.type) {
      const moreExtreme = f.type === "顶" ? f.price >= last.price : f.price <= last.price;
      if (moreExtreme) selected[selected.length - 1] = f;
      continue;
    }
    const separated = f.ci - last.ci >= 4;
    const validPrice = last.type === "底" ? f.price > last.price : f.price < last.price;
    if (separated && validPrice) selected.push(f);
  }
  return selected;
}
const penPoints = buildPens(fractals);
const pens = penPoints.slice(1).map((p, i) => {
  const a = penPoints[i];
  const indices = bars.slice(a.oi, p.oi + 1).map(x => x.i);
  const histArea = indices.reduce((s, idx) => s + Math.abs(macd[idx].hist), 0);
  return { n: i + 1, start: a, end: p, dir: p.price > a.price ? "上" : "下", high: Math.max(a.price, p.price), low: Math.min(a.price, p.price), histArea };
});

function buildSegments(points) {
  if (points.length < 4) return [];
  // 寻找首个“至少三笔且端点高低关系有效”的线段起点。
  let start = -1;
  for (let i = 0; i + 3 < points.length; i++) {
    const isBottomStart = points[i].type === "底";
    const valid = isBottomStart ? points[i + 3].price > points[i].price : points[i + 3].price < points[i].price;
    if (valid) { start = i; break; }
  }
  if (start < 0) return [];
  const anchors = [points[start]];
  while (start + 3 < points.length) {
    let end = start + 3;
    const up = points[start].type === "底";
    for (let j = start + 5; j < points.length; j += 2) {
      if ((up && points[j].price >= points[end].price) || (!up && points[j].price <= points[end].price)) end = j;
      else break;
    }
    anchors.push(points[end]);
    start = end;
  }
  return anchors.slice(1).map((p, i) => {
    const a = anchors[i];
    return { n: i + 1, start: a, end: p, dir: p.price > a.price ? "上" : "下", high: Math.max(a.price, p.price), low: Math.min(a.price, p.price) };
  });
}
const segments = buildSegments(penPoints);

function buildCenters(items) {
  const centers = [];
  for (let i = 0; i + 2 < items.length; i++) {
    const trio = items.slice(i, i + 3);
    const zg = Math.min(...trio.map(x => x.high));
    const zd = Math.max(...trio.map(x => x.low));
    if (zd < zg) {
      const start = trio[0].start.oi;
      let end = trio[2].end.oi;
      let j = i + 3;
      while (j < items.length && items[j].high >= zd && items[j].low <= zg) { end = items[j].end.oi; j++; }
      centers.push({ n: centers.length + 1, start, end, zd, zg, source: "线段", itemStart: i, itemEnd: j - 1 });
      i = j - 2;
    }
  }
  return centers;
}
let centers = buildCenters(segments);
let centerBasis = "线段";
if (!centers.length) {
  centers = buildCenters(pens);
  centerBasis = "笔（观察级别类中枢）";
  centers.forEach(c => c.source = centerBasis);
}

function classifySignals() {
  const sig = [];
  // 第一类：同向离开中枢后创新高/低、MACD柱面积相对前一同向笔衰减。
  for (let i = 2; i < pens.length; i++) {
    const p = pens[i], prevSame = pens[i - 2];
    const c = [...centers].reverse().find(z => z.end <= p.end.oi);
    if (!c) continue;
    if (p.dir === "下" && p.end.price < prevSame.end.price && p.histArea < prevSame.histArea * 0.9)
      sig.push({ type: "一买", oi: p.end.oi, date: p.end.date, price: p.end.price, reason: "下跌离开中枢后创新低，MACD柱面积较前一同向笔衰减" });
    if (p.dir === "上" && p.end.price > prevSame.end.price && p.histArea < prevSame.histArea * 0.9)
      sig.push({ type: "一卖", oi: p.end.oi, date: p.end.date, price: p.end.price, reason: "上涨离开中枢后创新高，MACD柱面积较前一同向笔衰减" });
  }
  // 第二类：一类点之后首次反向笔结束后的回试终点。
  for (const s of sig.filter(x => x.type === "一买" || x.type === "一卖")) {
    const idx = penPoints.findIndex(p => p.oi === s.oi);
    if (idx >= 0 && idx + 2 < penPoints.length) {
      const p = penPoints[idx + 2];
      sig.push({ type: s.type === "一买" ? "二买" : "二卖", oi: p.oi, date: p.date, price: p.price, reason: "第一类点后首次反向笔完成，再次回试的结束点" });
    }
  }
  // 第三类：离开中枢后首次回试不进入中枢。
  for (const c of centers) {
    for (let i = 0; i + 1 < pens.length; i++) {
      const leave = pens[i], pullback = pens[i + 1];
      if (leave.start.oi < c.end) continue;
      if (leave.end.price > c.zg && pullback.dir === "下" && pullback.end.price > c.zg) {
        sig.push({ type: "三买", oi: pullback.end.oi, date: pullback.end.date, price: pullback.end.price, reason: `向上离开中枢[${c.zd.toFixed(2)}, ${c.zg.toFixed(2)}]后回试不进入` });
        break;
      }
      if (leave.end.price < c.zd && pullback.dir === "上" && pullback.end.price < c.zd) {
        sig.push({ type: "三卖", oi: pullback.end.oi, date: pullback.end.date, price: pullback.end.price, reason: `向下离开中枢[${c.zd.toFixed(2)}, ${c.zg.toFixed(2)}]后回试不进入` });
        break;
      }
    }
  }
  const uniq = new Map();
  for (const s of sig.sort((a, b) => a.oi - b.oi)) uniq.set(`${s.type}-${s.oi}`, s);
  return [...uniq.values()];
}
const signals = classifySignals();

function esc(s) { return String(s).replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&apos;" }[c])); }
function makeSvg() {
  const W = 1700, H = 1000, left = 90, right = 40, top = 85, priceH = 690, volTop = 810, volH = 115;
  const plotW = W - left - right;
  const minP = Math.min(...bars.map(b => b.low)) * 0.95;
  const maxP = Math.max(...bars.map(b => b.high)) * 1.05;
  const maxV = Math.max(...bars.map(b => b.volume));
  const x = i => left + (i + 0.5) * plotW / bars.length;
  const y = p => top + (maxP - p) * priceH / (maxP - minP);
  const vy = v => volTop + volH - v * volH / maxV;
  const step = plotW / bars.length, bodyW = Math.max(3, step * 0.55);
  const parts = [`<svg xmlns="http://www.w3.org/2000/svg" width="${W}" height="${H}" viewBox="0 0 ${W} ${H}">`,
    `<rect width="${W}" height="${H}" fill="#F7F9FC"/>`,
    `<text x="${left}" y="38" font-family="Arial, sans-serif" font-size="25" font-weight="700" fill="#172B4D">长电科技（600584）日线缠论结构图</text>`,
    `<text x="${left}" y="65" font-family="Arial, sans-serif" font-size="14" fill="#5E6C84">${fmtDate(bars[0].date)} — ${fmtDate(bars.at(-1).date)} · 红涨绿跌 · 蓝色笔 · 紫色线段 · 黄色中枢</text>`];
  for (let k = 0; k <= 6; k++) {
    const p = minP + (maxP - minP) * k / 6, yy = y(p);
    parts.push(`<line x1="${left}" y1="${yy}" x2="${W - right}" y2="${yy}" stroke="#DFE3E8" stroke-width="1"/>`);
    parts.push(`<text x="${left - 12}" y="${yy + 5}" text-anchor="end" font-family="Arial" font-size="12" fill="#6B778C">${p.toFixed(2)}</text>`);
  }
  for (const c of centers) {
    parts.push(`<rect x="${x(c.start) - step / 2}" y="${y(c.zg)}" width="${Math.max(step, x(c.end) - x(c.start) + step)}" height="${Math.max(4, y(c.zd) - y(c.zg))}" fill="#FFD54F" fill-opacity="0.20" stroke="#C49A00" stroke-width="2" stroke-dasharray="7 5"/>`);
    parts.push(`<text x="${x(c.start)}" y="${y(c.zg) - 7}" font-family="Arial" font-size="12" font-weight="700" fill="#8A6D00">中枢${c.n} ${c.zd.toFixed(2)}–${c.zg.toFixed(2)}</text>`);
  }
  bars.forEach((b, i) => {
    const up = b.close >= b.open, color = up ? "#D64545" : "#1F9D72";
    parts.push(`<line x1="${x(i)}" y1="${y(b.high)}" x2="${x(i)}" y2="${y(b.low)}" stroke="${color}" stroke-width="1.6"/>`);
    const by = Math.min(y(b.open), y(b.close)), bh = Math.max(1.8, Math.abs(y(b.open) - y(b.close)));
    parts.push(`<rect x="${x(i) - bodyW / 2}" y="${by}" width="${bodyW}" height="${bh}" fill="${up ? color : "#F7F9FC"}" stroke="${color}" stroke-width="1.4"/>`);
    parts.push(`<rect x="${x(i) - bodyW / 2}" y="${vy(b.volume)}" width="${bodyW}" height="${volTop + volH - vy(b.volume)}" fill="${color}" fill-opacity="0.55"/>`);
  });
  for (const f of fractals) {
    const yy = y(f.price), offset = f.type === "顶" ? -10 : 17;
    parts.push(`<text x="${x(f.oi)}" y="${yy + offset}" text-anchor="middle" font-family="Arial" font-size="12" font-weight="700" fill="${f.type === "顶" ? "#C62828" : "#087F5B"}">${f.type}</text>`);
  }
  if (penPoints.length > 1) {
    parts.push(`<polyline points="${penPoints.map(p => `${x(p.oi)},${y(p.price)}`).join(" ")}" fill="none" stroke="#1976D2" stroke-width="3" stroke-linejoin="round"/>`);
    penPoints.forEach(p => parts.push(`<circle cx="${x(p.oi)}" cy="${y(p.price)}" r="4" fill="#FFFFFF" stroke="#1976D2" stroke-width="2"/>`));
  }
  if (segments.length) {
    const pts = [segments[0].start, ...segments.map(s => s.end)];
    parts.push(`<polyline points="${pts.map(p => `${x(p.oi)},${y(p.price)}`).join(" ")}" fill="none" stroke="#7E57C2" stroke-width="5" stroke-dasharray="11 7" stroke-linejoin="round" opacity="0.9"/>`);
  }
  for (const s of signals) {
    const buy = s.type.includes("买"), yy = y(s.price);
    parts.push(`<circle cx="${x(s.oi)}" cy="${yy}" r="8" fill="${buy ? "#1565C0" : "#E65100"}" stroke="#FFFFFF" stroke-width="2"/>`);
    parts.push(`<text x="${x(s.oi)}" y="${yy + (buy ? 26 : -14)}" text-anchor="middle" font-family="Arial" font-size="13" font-weight="700" fill="${buy ? "#0D47A1" : "#BF360C"}">${esc(s.type)}</text>`);
  }
  for (let i = 0; i < bars.length; i += Math.max(1, Math.floor(bars.length / 8))) {
    parts.push(`<text x="${x(i)}" y="${volTop + volH + 25}" text-anchor="middle" font-family="Arial" font-size="12" fill="#6B778C">${fmtDate(bars[i].date).slice(5)}</text>`);
  }
  parts.push(`<line x1="${left}" y1="${volTop - 12}" x2="${W - right}" y2="${volTop - 12}" stroke="#B3BAC5"/>`);
  parts.push(`<text x="${left}" y="${volTop - 20}" font-family="Arial" font-size="12" fill="#6B778C">成交量</text>`);
  parts.push(`<text x="${W - right}" y="${H - 18}" text-anchor="end" font-family="Arial" font-size="11" fill="#8993A4">自动结构识别，仅用于复盘研究，不构成投资建议</text></svg>`);
  return parts.join("");
}

await fs.mkdir(outputDir, { recursive: true });
const svg = makeSvg();
await fs.writeFile(svgPath, svg);
await sharp(Buffer.from(svg)).png().toFile(pngPath);
const pngBase64 = (await fs.readFile(pngPath)).toString("base64");

for (const name of ["缠论图", "结构明细", "计算辅助", "判定口径"]) {
  const existing = workbook.worksheets.items.find(s => s.name === name);
  if (existing) existing.delete();
}
const chartSheet = workbook.worksheets.add("缠论图");
const detail = workbook.worksheets.add("结构明细");
const calc = workbook.worksheets.add("计算辅助");
const rules = workbook.worksheets.add("判定口径");

const navy = "#172B4D", blue = "#1976D2", lightBlue = "#E9F2FF", gold = "#FFF3CD", green = "#E3FCEF", red = "#FFEBE6", gray = "#F4F5F7";
for (const sh of [source, chartSheet, detail, calc, rules]) sh.showGridLines = false;

// 原始数据：只格式化现有数据，不改值。
source.getRange("A1:M1").format = { fill: navy, font: { bold: true, color: "#FFFFFF" }, rowHeight: 26, horizontalAlignment: "center" };
source.getRange(`C2:C${rows.length + 1}`).format.numberFormat = "yyyy-mm-dd";
source.getRange(`D2:I${rows.length + 1}`).format.numberFormat = "0.00";
source.getRange(`J2:M${rows.length + 1}`).format.numberFormat = "#,##0";
source.freezePanes.freezeRows(1);

// 主图页
chartSheet.getRange("A1:N1").merge();
chartSheet.getRange("A1").values = [["600584 长电科技｜日线缠论自动结构分析"]];
chartSheet.getRange("A1:N1").format = { fill: navy, font: { bold: true, color: "#FFFFFF", size: 18 }, rowHeight: 34, verticalAlignment: "center" };
chartSheet.getRange("A2:N2").merge();
chartSheet.getRange("A2").values = [[`数据区间 ${fmtDate(bars[0].date)} 至 ${fmtDate(bars.at(-1).date)}｜${bars.length}根日K｜${compressed.length}根去包含K线｜${fractals.length}个分型｜${pens.length}笔｜${segments.length}线段｜${centers.length}中枢｜${signals.length}个买卖点`]];
chartSheet.getRange("A2:N2").format = { fill: lightBlue, font: { color: navy, size: 11 }, rowHeight: 24 };
chartSheet.getRange("A3:N3").merge();
chartSheet.getRange("A3").values = [[centers.length && centerBasis !== "线段" ? "提示：当前样本未形成足够的严格线段级中枢，图中中枢按三笔重叠显示为“观察级别类中枢”。" : "图中中枢由连续三线段重叠确认；信号仅在满足自动化规则时标注。"]];
chartSheet.getRange("A3:N3").format = { fill: gold, font: { color: "#6B5200", italic: false }, rowHeight: 24 };
chartSheet.images.add({ dataUrl: `data:image/png;base64,${pngBase64}`, anchor: { from: { row: 3, col: 0 }, extent: { widthPx: 1360, heightPx: 800 } } });
for (let c = 0; c < 14; c++) chartSheet.getRangeByIndexes(0, c, 1, 1).format.columnWidthPx = 98;
for (let r = 3; r < 43; r++) chartSheet.getRangeByIndexes(r, 0, 1, 1).format.rowHeightPx = 20;
chartSheet.freezePanes.freezeRows(3);

// 结构明细
detail.getRange("A1:J1").merge();
detail.getRange("A1").values = [["结构明细与信号审计"]];
detail.getRange("A1:J1").format = { fill: navy, font: { bold: true, color: "#FFFFFF", size: 16 }, rowHeight: 32 };
detail.getRange("A3:F3").values = [["分型类型", "日期", "价格", "去包含序号", "原K序号", "是否成笔端点"]];
detail.getRange(`A4:F${3 + fractals.length}`).values = fractals.map(f => [f.type, f.date, f.price, f.ci + 1, f.oi + 1, penPoints.some(p => p.oi === f.oi) ? "是" : "否"]);
detail.getRange(`B4:B${3 + fractals.length}`).format.numberFormat = "yyyy-mm-dd";
detail.getRange(`C4:C${3 + fractals.length}`).format.numberFormat = "0.00";
const penStart = 5 + fractals.length;
detail.getRange(`A${penStart}:H${penStart}`).values = [["笔序号", "方向", "起点日期", "起点价", "终点日期", "终点价", "MACD柱面积", "跨度(日K)"]];
detail.getRange(`A${penStart + 1}:H${penStart + pens.length}`).values = pens.map(p => [p.n, p.dir, p.start.date, p.start.price, p.end.date, p.end.price, p.histArea, p.end.oi - p.start.oi + 1]);
detail.getRange(`C${penStart + 1}:C${penStart + pens.length}`).format.numberFormat = "yyyy-mm-dd";
detail.getRange(`E${penStart + 1}:E${penStart + pens.length}`).format.numberFormat = "yyyy-mm-dd";
detail.getRange(`D${penStart + 1}:G${penStart + pens.length}`).format.numberFormat = "0.00";
const segStart = penStart + pens.length + 2;
detail.getRange(`A${segStart}:F${segStart}`).values = [["线段序号", "方向", "起点日期", "起点价", "终点日期", "终点价"]];
if (segments.length) {
  detail.getRange(`A${segStart + 1}:F${segStart + segments.length}`).values = segments.map(s => [s.n, s.dir, s.start.date, s.start.price, s.end.date, s.end.price]);
  detail.getRange(`C${segStart + 1}:C${segStart + segments.length}`).format.numberFormat = "yyyy-mm-dd";
  detail.getRange(`E${segStart + 1}:E${segStart + segments.length}`).format.numberFormat = "yyyy-mm-dd";
}
const centerStart = segStart + Math.max(1, segments.length) + 2;
detail.getRange(`A${centerStart}:G${centerStart}`).values = [["中枢序号", "构件口径", "起点日期", "终点日期", "ZD下沿", "ZG上沿", "区间宽度"]];
if (centers.length) {
  detail.getRange(`A${centerStart + 1}:G${centerStart + centers.length}`).values = centers.map(c => [c.n, c.source, bars[c.start].date, bars[c.end].date, c.zd, c.zg, c.zg - c.zd]);
  detail.getRange(`C${centerStart + 1}:D${centerStart + centers.length}`).format.numberFormat = "yyyy-mm-dd";
  detail.getRange(`E${centerStart + 1}:G${centerStart + centers.length}`).format.numberFormat = "0.00";
}
const signalStart = centerStart + Math.max(1, centers.length) + 2;
detail.getRange(`A${signalStart}:F${signalStart}`).values = [["信号", "日期", "价格", "原K序号", "判定依据", "状态"]];
if (signals.length) {
  detail.getRange(`A${signalStart + 1}:F${signalStart + signals.length}`).values = signals.map(s => [s.type, s.date, s.price, s.oi + 1, s.reason, "已确认"]);
  detail.getRange(`B${signalStart + 1}:B${signalStart + signals.length}`).format.numberFormat = "yyyy-mm-dd";
  detail.getRange(`C${signalStart + 1}:C${signalStart + signals.length}`).format.numberFormat = "0.00";
} else {
  detail.getRange(`A${signalStart + 1}:F${signalStart + 1}`).merge();
  detail.getRange(`A${signalStart + 1}`).values = [["当前数据区间内没有满足所述自动规则的已确认一、二、三类买卖点；未强行补标。"]];
}
for (const r of [3, penStart, segStart, centerStart, signalStart]) {
  detail.getRange(`A${r}:J${r}`).format = { fill: blue, font: { bold: true, color: "#FFFFFF" }, rowHeight: 24 };
}
detail.getRange("A:J").format.wrapText = true;
detail.getRange("A:J").format.autofitColumns();
detail.getRange("E:E").format.columnWidth = 18;
detail.freezePanes.freezeRows(3);

// 计算辅助：MACD保持公式驱动。
calc.getRange("A1:M1").merge();
calc.getRange("A1").values = [["计算辅助（EMA / MACD / 去包含映射）"]];
calc.getRange("A1:M1").format = { fill: navy, font: { bold: true, color: "#FFFFFF", size: 16 }, rowHeight: 32 };
calc.getRange("A3:M3").values = [["序号", "日期", "开盘", "最高", "最低", "收盘", "EMA12", "EMA26", "DIF", "DEA", "MACD柱", "去包含序号", "分型"]];
calc.getRange(`A4:F${3 + bars.length}`).values = bars.map((b, i) => [i + 1, b.date, b.open, b.high, b.low, b.close]);
calc.getRange("G4").formulas = [["=F4"]];
calc.getRange("H4").formulas = [["=F4"]];
calc.getRange("I4").formulas = [["=G4-H4"]];
calc.getRange("J4").formulas = [["=I4"]];
calc.getRange("K4").formulas = [["=2*(I4-J4)"]];
if (bars.length > 1) {
  calc.getRange("G5").formulas = [["=G4*11/13+F5*2/13"]];
  calc.getRange(`G5:G${3 + bars.length}`).fillDown();
  calc.getRange("H5").formulas = [["=H4*25/27+F5*2/27"]];
  calc.getRange(`H5:H${3 + bars.length}`).fillDown();
  calc.getRange("I5").formulas = [["=G5-H5"]];
  calc.getRange(`I5:I${3 + bars.length}`).fillDown();
  calc.getRange("J5").formulas = [["=J4*8/10+I5*2/10"]];
  calc.getRange(`J5:J${3 + bars.length}`).fillDown();
  calc.getRange("K5").formulas = [["=2*(I5-J5)"]];
  calc.getRange(`K5:K${3 + bars.length}`).fillDown();
}
calc.getRange(`L4:M${3 + bars.length}`).values = bars.map((b, i) => {
  const ci = compressed.findIndex(x => i >= x.start && i <= x.end);
  const f = fractals.find(x => x.oi === i);
  return [ci + 1, f?.type || ""];
});
calc.getRange(`B4:B${3 + bars.length}`).format.numberFormat = "yyyy-mm-dd";
calc.getRange(`C4:K${3 + bars.length}`).format.numberFormat = "0.0000";
calc.getRange("A3:M3").format = { fill: blue, font: { bold: true, color: "#FFFFFF" }, rowHeight: 24 };
calc.freezePanes.freezeRows(3);
calc.getRange("A:M").format.autofitColumns();

// 判定口径
rules.getRange("A1:H1").merge();
rules.getRange("A1").values = [["判定口径、原书依据与局限"]];
rules.getRange("A1:H1").format = { fill: navy, font: { bold: true, color: "#FFFFFF", size: 16 }, rowHeight: 32 };
rules.getRange("A3:D3").values = [["模块", "本工作簿自动判定口径", "原书依据", "备注"]];
const ruleRows = [
  ["K线包含", "先按趋势方向处理包含：向上取高高，向下取低低；再识别分型。", "第62、65课：分型、笔与线段；再说分型、笔和线段", "用于消除相邻K线包含关系。"],
  ["顶/底分型", "去包含后，中间K线高低点均高于两侧为顶分型；均低于两侧为底分型。", "第62课", "所有分型均标注；只有满足成笔条件者连接为笔。"],
  ["笔", "顶底交替；相邻端点在去包含序列中至少间隔4位（含端点至少5根）；同类分型保留更极端者。", "第62、65课；第110课提到一笔至少延伸出6个基本K线单位", "自动化采用可复核的严格间隔口径。"],
  ["线段", "至少三笔；用反向笔对前一同向结构的破坏确认，端点按笔序列的更极端点延伸。", "第67、71、78课", "完整特征序列划分含复杂缺口情形；本表采用保守简化实现并单列明细。"],
  ["中枢", `优先取连续三线段价格区间重叠：[max(低点), min(高点)]；若样本不足，则显示三笔重叠的“观察级别类中枢”。当前口径：${centerBasis}。`, "第17、18课：至少三个连续次级别走势类型重叠", "笔不能构成严格中枢，降级显示会明确标注，不冒充严格线段级中枢。"],
  ["第一类买卖点", "离开最近中枢的同向笔创新高/低，同时MACD柱绝对面积较前一同向笔缩小10%以上，标作背驰候选的一类点。", "第17课；第24、28课；第101课称第一类点为背驰点", "MACD仅为辅助；日线样本无法递归到次级别，因此属于自动候选。"],
  ["第二类买卖点", "第一类点后首次反向笔完成，再次回试的结束点。", "第17课；第101课", "必须等待该笔完成，未完成不标。"],
  ["第三类买卖点", "价格离开中枢后，首次回试不重新进入中枢区间。", "第20、21、24、101课", "第三类点是中枢破坏点。"],
  ["数据与风险", `原始文件：K线导出_600584_日线数据.xlsx；共${bars.length}根日K。`, "数据来源单元格注明：妙想Choice", "仅供学习复盘，不构成投资建议。历史结构会随新增K线得到进一步确认或修正。"],
];
rules.getRange(`A4:D${3 + ruleRows.length}`).values = ruleRows;
rules.getRange("A3:D3").format = { fill: blue, font: { bold: true, color: "#FFFFFF" }, rowHeight: 24 };
rules.getRange(`A4:A${3 + ruleRows.length}`).format = { fill: gray, font: { bold: true, color: navy } };
rules.getRange("A:D").format.wrapText = true;
rules.getRange("A:A").format.columnWidth = 18;
rules.getRange("B:B").format.columnWidth = 54;
rules.getRange("C:C").format.columnWidth = 42;
rules.getRange("D:D").format.columnWidth = 38;
rules.getRange(`A4:D${3 + ruleRows.length}`).format.rowHeight = 58;
rules.freezePanes.freezeRows(3);

workbook.comments.setSelf({ displayName: "User" });
workbook.comments.addThread({ cell: rules.getRange("B8") }, "自动化中枢识别优先使用线段。若样本内线段不足，降级到三笔重叠只作观察，并在主图和规则页同时提示。");

const keyInspect = await workbook.inspect({
  kind: "table",
  range: `结构明细!A1:J${Math.min(signalStart + Math.max(2, signals.length + 1), 80)}`,
  include: "values,formulas",
  tableMaxRows: 80,
  tableMaxCols: 10,
  maxChars: 18000,
});
console.log(keyInspect.ndjson);
const errors = await workbook.inspect({
  kind: "match",
  searchTerm: "#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A",
  options: { useRegex: true, maxResults: 300 },
  summary: "final formula error scan",
});
console.log(errors.ndjson);

const output = await SpreadsheetFile.exportXlsx(workbook);
await output.save(outputPath);
const rendered = await workbook.render({ sheetName: "缠论图", range: "A1:N43", scale: 1, format: "png" });
await fs.writeFile(previewPath, new Uint8Array(await rendered.arrayBuffer()));
console.log(JSON.stringify({
  outputPath, previewPath, bars: bars.length, compressed: compressed.length,
  fractals: fractals.length, penPoints: penPoints.length, pens: pens.length,
  segments: segments.length, centers: centers.length, centerBasis, signals,
}));
