import fs from "node:fs/promises";
import sharp from "sharp";
import { SpreadsheetFile, Workbook } from "@oai/artifact-tool";

const outputDir = "/Users/shibicheng/shilun_standalone/outputs/ziyang_chan_2026";
const outputPath = `${outputDir}/上证指数_2026年以来日K箱体_紫阳东路买点.xlsx`;
const pngPath = `${outputDir}/上证指数_日K箱体_紫阳东路买点.png`;
const previewPath = `${outputDir}/final_preview.png`;
const payload = JSON.parse(await fs.readFile(`${outputDir}/chart_source.json`, "utf8"));

const d8 = v => String(Math.trunc(Number(v))).padStart(8, "0");
const dateOf = v => new Date(Date.UTC(+d8(v).slice(0, 4), +d8(v).slice(4, 6) - 1, +d8(v).slice(6, 8)));
const fmtDate = d => d.toISOString().slice(0, 10);
const bars = payload.index.map((r, i) => ({
  i, date: dateOf(r.trade_date), open: +r.open, high: +r.high, low: +r.low, close: +r.close,
  volume: +r.vol, amount: +r.amount, pct: +r.pct_chg, ma5: +r.ma5, ma10: +r.ma10,
  ma20: +r.ma20, shape: r.index_shape || "",
})).sort((a, b) => a.date - b.date).map((b, i) => ({ ...b, i }));
const dateIndex = new Map(bars.map((b, i) => [fmtDate(b.date).replaceAll("-", ""), i]));

const summary = payload.summary.map(r => ({
  dateKey: d8(r.anchor_date), type: r.event_type, eventCount: +r.event_count,
  stockCount: +r.stock_count, buy: +r.total_buy_amount, maxBuy: +r.max_single_buy_amount,
  shape: r.index_shape || "", return5: r.return_5d == null ? null : +r.return_5d,
})).filter(r => dateIndex.has(r.dateKey));
const exactByDate = new Map(summary.filter(r => r.type === "EXACT_BUY").map(r => [r.dateKey, r]));
const windowByDate = new Map(summary.filter(r => r.type === "WINDOW_BUY").map(r => [r.dateKey, r]));
const exactAmounts = [...exactByDate.values()].map(x => x.buy).sort((a, b) => a - b);
const focusThreshold = exactAmounts[Math.max(0, Math.floor(exactAmounts.length * 0.8))] || Infinity;
const focusDates = new Set([...exactByDate.values()].filter(x => x.buy >= focusThreshold || x.buy >= 100_000_000).map(x => x.dateKey));

// 价格箱体：从左到右扩展区间，最高—最低振幅不超过中位价的6%，
// 且至少持续10个交易日；突破后从下一交易日重新识别。
const priceBoxes = [];
for (let start = 0; start < bars.length;) {
  let end = start, low = bars[start].low, high = bars[start].high;
  while (end + 1 < bars.length) {
    const nextLow = Math.min(low, bars[end + 1].low);
    const nextHigh = Math.max(high, bars[end + 1].high);
    const widthRatio = (nextHigh - nextLow) / ((nextHigh + nextLow) / 2);
    if (widthRatio > 0.06) break;
    end += 1; low = nextLow; high = nextHigh;
  }
  if (end - start + 1 >= 10) {
    priceBoxes.push({
      n: priceBoxes.length + 1, start, end, low, high,
      widthRatio: (high - low) / ((high + low) / 2),
    });
  }
  start = Math.max(end + 1, start + 1);
}

function compressInclusions(data) {
  const out = []; let dir = 0;
  for (const b of data) {
    const cur = { ...b, start: b.i, end: b.i, sourceCount: 1 };
    if (!out.length) { out.push(cur); continue; }
    const last = out.at(-1);
    const contained = (cur.high <= last.high && cur.low >= last.low) || (cur.high >= last.high && cur.low <= last.low);
    if (!contained) {
      if (cur.high > last.high && cur.low > last.low) dir = 1;
      else if (cur.high < last.high && cur.low < last.low) dir = -1;
      out.push(cur); continue;
    }
    if (!dir && out.length >= 2) dir = last.high >= out.at(-2).high ? 1 : -1;
    if (!dir) dir = cur.close >= last.close ? 1 : -1;
    out[out.length - 1] = {
      ...last, end: cur.end, sourceCount: last.sourceCount + 1, close: cur.close,
      high: dir > 0 ? Math.max(last.high, cur.high) : Math.min(last.high, cur.high),
      low: dir > 0 ? Math.max(last.low, cur.low) : Math.min(last.low, cur.low),
    };
  }
  return out;
}
const compressed = compressInclusions(bars);
const fractals = [];
for (let i = 1; i < compressed.length - 1; i++) {
  const a = compressed[i - 1], b = compressed[i], c = compressed[i + 1];
  if (b.high > a.high && b.high > c.high && b.low > a.low && b.low > c.low)
    fractals.push({ type: "顶", ci: i, oi: b.end, date: bars[b.end].date, price: b.high });
  if (b.high < a.high && b.high < c.high && b.low < a.low && b.low < c.low)
    fractals.push({ type: "底", ci: i, oi: b.end, date: bars[b.end].date, price: b.low });
}
const penPoints = [];
for (const f of fractals) {
  if (!penPoints.length) { penPoints.push(f); continue; }
  const last = penPoints.at(-1);
  if (f.type === last.type) {
    if ((f.type === "顶" && f.price >= last.price) || (f.type === "底" && f.price <= last.price)) penPoints[penPoints.length - 1] = f;
    continue;
  }
  if (f.ci - last.ci >= 4 && (last.type === "底" ? f.price > last.price : f.price < last.price)) penPoints.push(f);
}
const pens = penPoints.slice(1).map((p, i) => ({
  n: i + 1, start: penPoints[i], end: p, dir: p.price > penPoints[i].price ? "上" : "下",
  high: Math.max(p.price, penPoints[i].price), low: Math.min(p.price, penPoints[i].price),
}));

function buildSegments(points) {
  if (points.length < 4) return [];
  let start = -1;
  for (let i = 0; i + 3 < points.length; i++) {
    const valid = points[i].type === "底" ? points[i + 3].price > points[i].price : points[i + 3].price < points[i].price;
    if (valid) { start = i; break; }
  }
  if (start < 0) return [];
  const anchors = [points[start]];
  while (start + 3 < points.length) {
    let end = start + 3; const up = points[start].type === "底";
    for (let j = start + 5; j < points.length; j += 2) {
      if ((up && points[j].price >= points[end].price) || (!up && points[j].price <= points[end].price)) end = j;
      else break;
    }
    anchors.push(points[end]); start = end;
  }
  return anchors.slice(1).map((p, i) => ({
    n: i + 1, start: anchors[i], end: p, dir: p.price > anchors[i].price ? "上" : "下",
    high: Math.max(p.price, anchors[i].price), low: Math.min(p.price, anchors[i].price),
  }));
}
const segments = buildSegments(penPoints);
function centersFrom(items, source) {
  const z = [];
  for (let i = 0; i + 2 < items.length; i++) {
    const trio = items.slice(i, i + 3), zg = Math.min(...trio.map(x => x.high)), zd = Math.max(...trio.map(x => x.low));
    if (zd < zg) {
      let end = trio[2].end.oi, j = i + 3;
      while (j < items.length && items[j].high >= zd && items[j].low <= zg) { end = items[j].end.oi; j++; }
      z.push({ n: z.length + 1, start: trio[0].start.oi, end, zd, zg, source });
      i = j - 2;
    }
  }
  return z;
}
let centers = centersFrom(segments, "线段");
let centerBasis = "线段";
if (!centers.length) { centers = centersFrom(pens, "笔（观察级别类中枢）"); centerBasis = "笔（观察级别类中枢）"; }

function esc(s) { return String(s).replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&apos;" }[c])); }
function makeSvg() {
  const W = 1900, H = 1120, left = 100, right = 50, top = 85, priceH = 720, buyTop = 850, buyH = 165;
  const plotW = W - left - right, step = plotW / bars.length;
  const minP = Math.min(...bars.map(b => b.low)) * 0.98, maxP = Math.max(...bars.map(b => b.high)) * 1.02;
  const maxBuy = Math.max(...summary.map(x => x.buy), 1);
  const x = i => left + (i + 0.5) * step, y = p => top + (maxP - p) * priceH / (maxP - minP);
  const by = v => buyTop + buyH - v / maxBuy * buyH, bodyW = Math.max(2, step * 0.58);
  const p = [`<svg xmlns="http://www.w3.org/2000/svg" width="${W}" height="${H}" viewBox="0 0 ${W} ${H}">`,
    `<rect width="${W}" height="${H}" fill="#F7F9FC"/>`,
    `<text x="${left}" y="36" font-family="Arial" font-size="26" font-weight="700" fill="#172B4D">上证指数｜2026年以来日K趋势、价格箱体与紫阳东路公开买点</text>`,
    `<text x="${left}" y="64" font-family="Arial" font-size="14" fill="#5E6C84">${fmtDate(bars[0].date)}—${fmtDate(bars.at(-1).date)} · 蓝色矩形=价格箱体 · 洋红圆点=确切单日买榜 · 橙色菱形=累计窗口披露</text>`];
  for (let k = 0; k <= 6; k++) {
    const price = minP + (maxP - minP) * k / 6, yy = y(price);
    p.push(`<line x1="${left}" y1="${yy}" x2="${W - right}" y2="${yy}" stroke="#DFE3E8"/>`,
      `<text x="${left - 12}" y="${yy + 5}" text-anchor="end" font-family="Arial" font-size="12" fill="#6B778C">${price.toFixed(0)}</text>`);
  }
  for (const box of priceBoxes) {
    p.push(`<rect x="${x(box.start) - step / 2}" y="${y(box.high)}" width="${Math.max(step, x(box.end) - x(box.start) + step)}" height="${Math.max(4, y(box.low) - y(box.high))}" fill="#4C9AFF" fill-opacity=".08" stroke="#1976D2" stroke-width="2" stroke-dasharray="8 5"/>`,
      `<text x="${x(box.start)}" y="${y(box.high) - 8}" font-family="Arial" font-size="12" font-weight="700" fill="#0D47A1">箱体${box.n} ${box.low.toFixed(0)}–${box.high.toFixed(0)}</text>`);
  }
  bars.forEach((b, i) => {
    const up = b.close >= b.open, color = up ? "#D64545" : "#1F9D72";
    p.push(`<line x1="${x(i)}" y1="${y(b.high)}" x2="${x(i)}" y2="${y(b.low)}" stroke="${color}" stroke-width="1.2"/>`);
    const yy = Math.min(y(b.open), y(b.close)), hh = Math.max(1.3, Math.abs(y(b.open) - y(b.close)));
    p.push(`<rect x="${x(i) - bodyW / 2}" y="${yy}" width="${bodyW}" height="${hh}" fill="${up ? color : "#F7F9FC"}" stroke="${color}" stroke-width="1.1"/>`);
  });
  for (const [key, e] of exactByDate) {
    const i = dateIndex.get(key), yy = y(bars[i].low) + 14, r = focusDates.has(key) ? 6 : 3.2;
    p.push(`<circle cx="${x(i)}" cy="${yy}" r="${r}" fill="#C2185B" stroke="#FFFFFF" stroke-width="1"/>`);
  }
  for (const [key] of windowByDate) {
    const i = dateIndex.get(key), xx = x(i), yy = y(bars[i].high) - 13, s = 5;
    p.push(`<polygon points="${xx},${yy - s} ${xx + s},${yy} ${xx},${yy + s} ${xx - s},${yy}" fill="#FFFFFF" stroke="#EF6C00" stroke-width="2"/>`);
  }
  const labelEvents = [...exactByDate.values()].sort((a, b) => b.buy - a.buy).slice(0, 10);
  labelEvents.forEach((e, j) => {
    const i = dateIndex.get(e.dateKey), yy = y(bars[i].low) + 27 + (j % 2) * 13;
    p.push(`<text x="${x(i)}" y="${yy}" text-anchor="middle" font-family="Arial" font-size="10" font-weight="700" fill="#880E4F">${(e.buy / 1e8).toFixed(1)}亿</text>`);
  });
  p.push(`<line x1="${left}" y1="${buyTop - 18}" x2="${W - right}" y2="${buyTop - 18}" stroke="#B3BAC5"/>`,
    `<text x="${left}" y="${buyTop - 26}" font-family="Arial" font-size="12" fill="#6B778C">紫阳东路公开买入金额（亿元）</text>`);
  for (const [key, e] of exactByDate) {
    const i = dateIndex.get(key), yy = by(e.buy);
    p.push(`<rect x="${x(i) - Math.max(2, bodyW / 2)}" y="${yy}" width="${Math.max(4, bodyW)}" height="${buyTop + buyH - yy}" fill="#C2185B" fill-opacity=".62"/>`);
  }
  for (const [key, e] of windowByDate) {
    const i = dateIndex.get(key), yy = by(e.buy);
    p.push(`<rect x="${x(i) - Math.max(2, bodyW / 2)}" y="${yy}" width="${Math.max(4, bodyW)}" height="${buyTop + buyH - yy}" fill="none" stroke="#EF6C00" stroke-width="1.3"/>`);
  }
  for (let i = 0; i < bars.length; i += 16) p.push(`<text x="${x(i)}" y="${buyTop + buyH + 24}" text-anchor="middle" font-family="Arial" font-size="11" fill="#6B778C">${fmtDate(bars[i].date).slice(5)}</text>`);
  p.push(`<text x="${W - right}" y="${H - 18}" text-anchor="end" font-family="Arial" font-size="11" fill="#8993A4">公开龙虎榜仅反映营业部进入买入前五的披露记录，不代表全部交易，也不构成指数买入建议</text></svg>`);
  return p.join("");
}

const svg = makeSvg();
await sharp(Buffer.from(svg)).png().toFile(pngPath);
const pngBase64 = (await fs.readFile(pngPath)).toString("base64");

const workbook = Workbook.create();
const chart = workbook.worksheets.add("趋势图");
const daily = workbook.worksheets.add("上证日线");
const buySheet = workbook.worksheets.add("紫阳买入日");
const boxSheet = workbook.worksheets.add("价格箱体");
const rules = workbook.worksheets.add("口径说明");
for (const sh of workbook.worksheets.items) sh.showGridLines = false;
const navy = "#172B4D", blue = "#1976D2", lightBlue = "#E9F2FF", gold = "#FFF3CD";

chart.getRange("A1:N1").merge();
chart.getRange("A1").values = [["上证指数｜2026年以来日K趋势、价格箱体与紫阳东路公开买点"]];
chart.getRange("A1:N1").format = { fill: navy, font: { bold: true, color: "#FFFFFF", size: 18 }, rowHeight: 34 };
chart.getRange("A2:N2").merge();
chart.getRange("A2").values = [[`${bars.length}个交易日｜${priceBoxes.length}个价格箱体｜${exactByDate.size}个确切买入披露日｜${windowByDate.size}个累计窗口披露日`]];
chart.getRange("A2:N2").format = { fill: lightBlue, font: { color: navy }, rowHeight: 24 };
chart.getRange("A3:N3").merge();
chart.getRange("A3").values = [[`蓝色矩形为连续震荡价格箱体；洋红圆点是确切单日龙虎榜买入披露；橙色菱形是多日累计窗口，不能视为确切成交日。`]];
chart.getRange("A3:N3").format = { fill: gold, font: { color: "#6B5200" }, rowHeight: 24 };
chart.images.add({ dataUrl: `data:image/png;base64,${pngBase64}`, anchor: { from: { row: 3, col: 0 }, extent: { widthPx: 1460, heightPx: 860 } } });
for (let c = 0; c < 14; c++) chart.getRangeByIndexes(0, c, 1, 1).format.columnWidthPx = 105;
for (let r = 3; r < 46; r++) chart.getRangeByIndexes(r, 0, 1, 1).format.rowHeightPx = 20;
chart.freezePanes.freezeRows(3);

daily.getRange("A1:N1").merge(); daily.getRange("A1").values = [["上证指数日线与指标"]];
daily.getRange("A1:N1").format = { fill: navy, font: { bold: true, color: "#FFFFFF", size: 16 }, rowHeight: 32 };
daily.getRange("A3:N3").values = [["日期","开盘","最高","最低","收盘","涨跌幅%","成交量","成交额","MA5","MA10","MA20","指数形态","确切买入额","窗口买入额"]];
daily.getRange(`A4:L${3 + bars.length}`).values = bars.map(b => [b.date,b.open,b.high,b.low,b.close,b.pct,b.volume,b.amount,b.ma5,b.ma10,b.ma20,b.shape]);
daily.getRange(`M4:N${3 + bars.length}`).values = bars.map(b => {
  const k = fmtDate(b.date).replaceAll("-", "");
  return [exactByDate.get(k)?.buy || 0, windowByDate.get(k)?.buy || 0];
});
daily.getRange(`A4:A${3 + bars.length}`).format.numberFormat = "yyyy-mm-dd";
daily.getRange(`B4:F${3 + bars.length}`).format.numberFormat = "0.00";
daily.getRange(`G4:H${3 + bars.length}`).format.numberFormat = "#,##0";
daily.getRange(`I4:K${3 + bars.length}`).format.numberFormat = "0.00";
daily.getRange(`M4:N${3 + bars.length}`).format.numberFormat = "#,##0";
daily.getRange("A3:N3").format = { fill: blue, font: { bold: true, color: "#FFFFFF" }, rowHeight: 24 };
daily.getRange("A:N").format.autofitColumns(); daily.freezePanes.freezeRows(3);

buySheet.getRange("A1:J1").merge(); buySheet.getRange("A1").values = [["紫阳东路公开买入日期汇总"]];
buySheet.getRange("A1:J1").format = { fill: navy, font: { bold: true, color: "#FFFFFF", size: 16 }, rowHeight: 32 };
buySheet.getRange("A3:J3").values = [["日期","披露类型","事件数","股票数","总买入额","最大单笔","指数形态","指数5日收益","重点日期","口径说明"]];
const sumRows = summary.sort((a,b) => a.dateKey.localeCompare(b.dateKey) || a.type.localeCompare(b.type));
buySheet.getRange(`A4:J${3 + sumRows.length}`).values = sumRows.map(r => [dateOf(r.dateKey),r.type,r.eventCount,r.stockCount,r.buy,r.maxBuy,r.shape,r.return5,r.type==="EXACT_BUY"&&focusDates.has(r.dateKey)?"HIGH_FOCUS":"",r.type==="EXACT_BUY"?"确切单日买榜":"累计窗口结束日"]);
buySheet.getRange(`A4:A${3 + sumRows.length}`).format.numberFormat = "yyyy-mm-dd";
buySheet.getRange(`E4:F${3 + sumRows.length}`).format.numberFormat = "#,##0";
buySheet.getRange(`H4:H${3 + sumRows.length}`).format.numberFormat = "0.00%";
buySheet.getRange("A3:J3").format = { fill: blue, font: { bold: true, color: "#FFFFFF" }, rowHeight: 24 };
buySheet.getRange("A:J").format.autofitColumns(); buySheet.getRange("J:J").format.columnWidth = 20; buySheet.freezePanes.freezeRows(3);

boxSheet.getRange("A1:H1").merge(); boxSheet.getRange("A1").values = [["价格箱体明细"]];
boxSheet.getRange("A1:H1").format = { fill: navy, font: { bold: true, color: "#FFFFFF", size: 16 }, rowHeight: 32 };
boxSheet.getRange("A3:H3").values = [["箱体","开始日期","结束日期","交易日数","下沿","上沿","箱体振幅","突破后状态"]];
boxSheet.getRange(`A4:H${3 + priceBoxes.length}`).values = priceBoxes.map((b, i) => {
  const next = bars[b.end + 1];
  let status = "样本结束";
  if (next) status = next.high > b.high ? "向上突破" : next.low < b.low ? "向下突破" : "离开箱体";
  return [b.n,bars[b.start].date,bars[b.end].date,b.end-b.start+1,b.low,b.high,b.widthRatio,status];
});
boxSheet.getRange(`B4:C${3 + priceBoxes.length}`).format.numberFormat = "yyyy-mm-dd";
boxSheet.getRange(`E4:F${3 + priceBoxes.length}`).format.numberFormat = "0.00";
boxSheet.getRange(`G4:G${3 + priceBoxes.length}`).format.numberFormat = "0.00%";
boxSheet.getRange("A3:H3").format = { fill: blue, font: { bold: true, color: "#FFFFFF" }, rowHeight: 24 };
boxSheet.getRange("A:H").format.autofitColumns(); boxSheet.freezePanes.freezeRows(3);

rules.getRange("A1:D1").merge(); rules.getRange("A1").values = [["口径说明与数据边界"]];
rules.getRange("A1:D1").format = { fill: navy, font: { bold: true, color: "#FFFFFF", size: 16 }, rowHeight: 32 };
rules.getRange("A3:D3").values = [["项目","定义","来源/规则","注意事项"]];
const ruleRows = [
  ["研究区间",`${fmtDate(bars[0].date)}至${fmtDate(bars.at(-1).date)}`,"上证指数 000001.SH 日线","2026-01-01为节假日，首个交易日为2026-01-05。"],
  ["紫阳东路","国泰海通证券股份有限公司武汉紫阳东路证券营业部，兼容原国泰君安及名称变体。","Tushare top_inst；top_list交叉补充","公开龙虎榜是营业部汇总，不是客户逐笔交割单，也不是全部交易。"],
  ["确切买入点","统计窗口起止同日、营业部进入买入前五且买入额大于0。","图中洋红圆点及实心金额柱","这是披露意义上的买点，不等于指数技术买点。"],
  ["累计窗口","多日累计上榜记录，仅将period_end作为披露锚点。","图中橙色空心菱形及空心金额柱","不能把锚点说成真实成交发生日。"],
  ["价格箱体","从左到右扩展连续交易区间；区间最高价与最低价的振幅不超过中位价6%，且至少持续10个交易日。","使用原始日K最高价和最低价","突破阈值后结束当前箱体，并从下一交易日重新识别。"],
  ["重点日期",`确切买入额位于样本前20%或单日不低于1亿元；本图仅给金额最高10日文字标签。`,"只控制标注密度，不构成交易信号","所有日期仍在图中以圆点和金额柱保留。"],
  ["样本质量",`原始龙虎榜121,057行；目标营业部656行；去重买入事件366个，其中确切235、窗口131。`,"事件指数快照缺失0","数据截至2026-07-24。"],
];
rules.getRange(`A4:D${3 + ruleRows.length}`).values = ruleRows;
rules.getRange("A3:D3").format = { fill: blue, font: { bold: true, color: "#FFFFFF" }, rowHeight: 24 };
rules.getRange("A:D").format.wrapText = true; rules.getRange("A:A").format.columnWidth=18; rules.getRange("B:B").format.columnWidth=50; rules.getRange("C:C").format.columnWidth=38; rules.getRange("D:D").format.columnWidth=42;
rules.getRange(`A4:D${3 + ruleRows.length}`).format.rowHeight = 58; rules.freezePanes.freezeRows(3);

workbook.comments.setSelf({displayName:"User"});
workbook.comments.addThread({cell: rules.getRange("B6")}, "紫阳东路的“买点”按公开龙虎榜披露定义，不应理解为营业部当日全部成交，也不应直接当作指数买入信号。");

const inspect = await workbook.inspect({kind:"table",range:`紫阳买入日!A1:J${Math.min(3+sumRows.length,70)}`,include:"values,formulas",tableMaxRows:70,tableMaxCols:10,maxChars:16000});
console.log(inspect.ndjson);
const errors = await workbook.inspect({kind:"match",searchTerm:"#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A",options:{useRegex:true,maxResults:300},summary:"final formula error scan"});
console.log(errors.ndjson);
const out = await SpreadsheetFile.exportXlsx(workbook); await out.save(outputPath);
const rendered = await workbook.render({sheetName:"趋势图",range:"A1:N46",scale:1,format:"png"});
await fs.writeFile(previewPath,new Uint8Array(await rendered.arrayBuffer()));
console.log(JSON.stringify({outputPath,pngPath,previewPath,bars:bars.length,priceBoxes,exactDates:exactByDate.size,windowDates:windowByDate.size}));
