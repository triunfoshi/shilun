import fs from "node:fs/promises";
import sharp from "sharp";
import { SpreadsheetFile, Workbook } from "@oai/artifact-tool";

const outputDir = "/Users/shibicheng/shilun_standalone/outputs/ziyang_chan_2026";
const outputPath = `${outputDir}/上证指数_紫阳东路买入环境分析_优化版.xlsx`;
const pngPath = `${outputDir}/上证指数_紫阳东路买入环境_优化趋势图.png`;
const svgPath = `${outputDir}/上证指数_紫阳东路买入环境_优化趋势图.svg`;
const previewPath = `${outputDir}/behavior_preview.png`;
const payload = JSON.parse(await fs.readFile(`${outputDir}/chart_source.json`, "utf8"));
const opportunityRows = JSON.parse(await fs.readFile(`${outputDir}/opportunity_source.json`, "utf8"));

const d8 = value => String(Math.trunc(Number(value))).padStart(8, "0");
const dateOf = value => {
  const s = d8(value);
  return new Date(Date.UTC(+s.slice(0, 4), +s.slice(4, 6) - 1, +s.slice(6, 8)));
};
const fmtDate = date => date.toISOString().slice(0, 10);
const fmtMonth = key => `${key.slice(0, 4)}-${key.slice(4, 6)}`;
const sum = values => values.reduce((total, value) => total + (+value || 0), 0);
const mean = values => values.length ? sum(values) / values.length : 0;
const quantile = (values, q) => {
  const sorted = values.filter(Number.isFinite).slice().sort((a, b) => a - b);
  if (!sorted.length) return 0;
  const pos = (sorted.length - 1) * q;
  const lo = Math.floor(pos), hi = Math.ceil(pos), weight = pos - lo;
  return sorted[lo] * (1 - weight) + sorted[hi] * weight;
};
const median = values => quantile(values, 0.5);
const safeDiv = (a, b) => b ? a / b : 0;
const round = (value, digits = 4) => {
  const factor = 10 ** digits;
  return Math.round((value + Number.EPSILON) * factor) / factor;
};

function averageRanks(values) {
  const order = values.map((value, index) => ({ value, index })).sort((a, b) => a.value - b.value);
  const ranks = Array(values.length);
  for (let i = 0; i < order.length;) {
    let j = i + 1;
    while (j < order.length && order[j].value === order[i].value) j += 1;
    const rank = ((i + 1) + j) / 2;
    for (let k = i; k < j; k++) ranks[order[k].index] = rank;
    i = j;
  }
  return ranks;
}

function pearson(xs, ys) {
  if (xs.length < 2) return 0;
  const mx = mean(xs), my = mean(ys);
  let numerator = 0, dx = 0, dy = 0;
  for (let i = 0; i < xs.length; i++) {
    const a = xs[i] - mx, b = ys[i] - my;
    numerator += a * b; dx += a * a; dy += b * b;
  }
  return dx && dy ? numerator / Math.sqrt(dx * dy) : 0;
}

function spearman(rows, xKey, yKey) {
  const valid = rows.filter(row => Number.isFinite(row[xKey]) && Number.isFinite(row[yKey]));
  return pearson(averageRanks(valid.map(row => row[xKey])), averageRanks(valid.map(row => row[yKey])));
}

function solveLinear(matrix, vector) {
  const n = vector.length;
  const augmented = matrix.map((row, i) => [...row, vector[i]]);
  for (let col = 0; col < n; col++) {
    let pivot = col;
    for (let row = col + 1; row < n; row++) {
      if (Math.abs(augmented[row][col]) > Math.abs(augmented[pivot][col])) pivot = row;
    }
    [augmented[col], augmented[pivot]] = [augmented[pivot], augmented[col]];
    const divisor = augmented[col][col] || 1e-12;
    for (let j = col; j <= n; j++) augmented[col][j] /= divisor;
    for (let row = 0; row < n; row++) {
      if (row === col) continue;
      const factor = augmented[row][col];
      for (let j = col; j <= n; j++) augmented[row][j] -= factor * augmented[col][j];
    }
  }
  return augmented.map(row => row[n]);
}

function olsCoefficients(rows, outcome, monthKeys) {
  const xRows = rows.map(row => [
    1,
    row.shape === "M_UP" ? 1 : 0,
    row.shape === "M_DOWN" ? 1 : 0,
    ...monthKeys.slice(1).map(month => row.month === month ? 1 : 0),
  ]);
  const y = rows.map(row => row[outcome]);
  const p = xRows[0].length;
  const xtx = Array.from({ length: p }, () => Array(p).fill(0));
  const xty = Array(p).fill(0);
  for (let i = 0; i < xRows.length; i++) {
    for (let a = 0; a < p; a++) {
      xty[a] += xRows[i][a] * y[i];
      for (let b = 0; b < p; b++) xtx[a][b] += xRows[i][a] * xRows[i][b];
    }
  }
  return solveLinear(xtx, xty);
}

function chiSquareShape(rows, shapeKeys) {
  const table = shapeKeys.map(shape => {
    const group = rows.filter(row => row.shape === shape);
    const buys = group.filter(row => row.buyFlag).length;
    return [group.length - buys, buys];
  });
  const total = sum(table.flat());
  const rowTotals = table.map(row => sum(row));
  const colTotals = [sum(table.map(row => row[0])), sum(table.map(row => row[1]))];
  let chi2 = 0;
  for (let i = 0; i < table.length; i++) {
    for (let j = 0; j < 2; j++) {
      const expected = rowTotals[i] * colTotals[j] / total;
      chi2 += (table[i][j] - expected) ** 2 / expected;
    }
  }
  return { chi2, p: Math.exp(-chi2 / 2), cramerV: Math.sqrt(chi2 / total) };
}

function kruskalShape(rows, shapeKeys) {
  const buyRows = rows.filter(row => row.buyFlag);
  const ranks = averageRanks(buyRows.map(row => row.buyYi));
  const ranked = buyRows.map((row, i) => ({ ...row, rank: ranks[i] }));
  const n = ranked.length;
  let h = 12 / (n * (n + 1)) * sum(shapeKeys.map(shape => {
    const group = ranked.filter(row => row.shape === shape);
    return group.length * mean(group.map(row => row.rank)) ** 2;
  })) - 3 * (n + 1);
  const counts = new Map();
  for (const row of buyRows) counts.set(row.buyYi, (counts.get(row.buyYi) || 0) + 1);
  const tieCorrection = 1 - sum([...counts.values()].map(count => count ** 3 - count)) / (n ** 3 - n);
  h /= tieCorrection;
  return { h, p: Math.exp(-h / 2) };
}

const opportunityByDate = new Map(opportunityRows.map(row => [d8(row.trade_date), row]));
const summaryRows = payload.summary.map(row => ({
  dateKey: d8(row.anchor_date),
  type: row.event_type,
  eventCount: +row.event_count,
  stockCount: +row.stock_count,
  buy: +row.total_buy_amount,
  maxBuy: +row.max_single_buy_amount,
  shape: row.index_shape || "",
  return5: row.return_5d == null ? null : +row.return_5d,
}));
const exactByDate = new Map(summaryRows.filter(row => row.type === "EXACT_BUY").map(row => [row.dateKey, row]));
const windowByDate = new Map(summaryRows.filter(row => row.type === "WINDOW_BUY").map(row => [row.dateKey, row]));

const bars = payload.index
  .filter(row => d8(row.trade_date) >= "20260101")
  .map(row => {
    const dateKey = d8(row.trade_date);
    const exact = exactByDate.get(dateKey);
    const window = windowByDate.get(dateKey);
    const opportunity = opportunityByDate.get(dateKey) || {};
    const ma20 = +row.ma20;
    return {
      dateKey,
      date: dateOf(dateKey),
      month: dateKey.slice(0, 6),
      open: +row.open,
      high: +row.high,
      low: +row.low,
      close: +row.close,
      pct: +row.pct_chg,
      volume: +row.vol,
      amount: +row.amount,
      ma5: +row.ma5,
      ma10: +row.ma10,
      ma20,
      return1: +row.return_1d,
      return3: +row.return_3d,
      return5: +row.return_5d,
      return10: +row.return_10d,
      return20: +row.return_20d,
      ma20Slope: +row.ma20_5d_slope,
      amountRatio: +row.amount_ratio_5_20,
      drawdown20: +row.drawdown_from_20d_high,
      shape: row.index_shape || "UNKNOWN",
      exactBuy: exact?.buy || 0,
      buyYi: (exact?.buy || 0) / 1e8,
      eventCount: exact?.eventCount || 0,
      stockCount: exact?.stockCount || 0,
      windowBuy: window?.buy || 0,
      buyFlag: exact?.buy > 0 ? 1 : 0,
      ticketYi: exact?.stockCount ? exact.buy / 1e8 / exact.stockCount : 0,
      lhbRows: +opportunity.lhb_rows || 0,
      lhbStocks: +opportunity.lhb_stocks || 0,
      lhbBuyYi: (+opportunity.lhb_buy_amount || 0) / 1e8,
      ma20Gap: ma20 ? +row.close / ma20 - 1 : 0,
      boxNo: null,
      boxLow: null,
      boxHigh: null,
      boxPosition: null,
      boxBucket: "箱体外",
    };
  })
  .sort((a, b) => a.date - b.date)
  .map((row, index) => ({ ...row, i: index }));

const dateIndex = new Map(bars.map((row, index) => [row.dateKey, index]));
const shapeKeys = ["M_UP", "M_RANGE", "M_DOWN"];
const shapeLabel = { M_UP: "上行", M_RANGE: "震荡", M_DOWN: "下行" };
const shapeColor = { M_UP: "#2F6FDB", M_RANGE: "#7B8798", M_DOWN: "#D14D64" };
const shapeFill = { M_UP: "#EAF2FF", M_RANGE: "#F1F3F6", M_DOWN: "#FFF0F2" };

// 价格箱体：从左向右扩展，区间振幅不超过中位价6%，且至少10个交易日。
const priceBoxes = [];
for (let start = 0; start < bars.length;) {
  let end = start, low = bars[start].low, high = bars[start].high;
  while (end + 1 < bars.length) {
    const nextLow = Math.min(low, bars[end + 1].low);
    const nextHigh = Math.max(high, bars[end + 1].high);
    const widthRatio = (nextHigh - nextLow) / ((nextHigh + nextLow) / 2);
    if (widthRatio > 0.06) break;
    end += 1;
    low = nextLow;
    high = nextHigh;
  }
  if (end - start + 1 >= 10) {
    priceBoxes.push({
      n: priceBoxes.length + 1,
      start,
      end,
      low,
      high,
      widthRatio: (high - low) / ((high + low) / 2),
    });
  }
  start = Math.max(end + 1, start + 1);
}
for (const box of priceBoxes) {
  for (let i = box.start; i <= box.end; i++) {
    const position = safeDiv(bars[i].close - box.low, box.high - box.low);
    bars[i].boxNo = box.n;
    bars[i].boxLow = box.low;
    bars[i].boxHigh = box.high;
    bars[i].boxPosition = position;
    bars[i].boxBucket = position < 1 / 3 ? "下沿区" : position < 2 / 3 ? "中部区" : "上沿区";
  }
}

function summarize(rows, key, value) {
  const group = rows.filter(row => row[key] === value);
  const buyRows = group.filter(row => row.buyFlag);
  return {
    key: value,
    days: group.length,
    buyDays: buyRows.length,
    rate: safeDiv(buyRows.length, group.length),
    totalYi: sum(group.map(row => row.buyYi)),
    perDayYi: safeDiv(sum(group.map(row => row.buyYi)), group.length),
    avgBuyDayYi: mean(buyRows.map(row => row.buyYi)),
    medianBuyDayYi: median(buyRows.map(row => row.buyYi)),
    meanStocks: mean(buyRows.map(row => row.stockCount)),
    meanTicketYi: mean(buyRows.map(row => row.ticketYi)),
    aggregateTicketYi: safeDiv(sum(buyRows.map(row => row.buyYi)), sum(buyRows.map(row => row.stockCount))),
    avgLhbStocks: mean(group.map(row => row.lhbStocks)),
  };
}

const shapeStats = shapeKeys.map(shape => summarize(bars, "shape", shape));
const monthKeys = [...new Set(bars.map(row => row.month))];
const monthStats = monthKeys.map(month => {
  const group = bars.filter(row => row.month === month);
  const buyRows = group.filter(row => row.buyFlag);
  return {
    key: month,
    days: group.length,
    buyDays: buyRows.length,
    rate: safeDiv(buyRows.length, group.length),
    totalYi: sum(group.map(row => row.buyYi)),
    avgBuyDayYi: mean(buyRows.map(row => row.buyYi)),
    avgLhbStocks: mean(group.map(row => row.lhbStocks)),
  };
});

const p80 = quantile(bars.filter(row => row.buyFlag).map(row => row.buyYi), 0.8);
const totalBuyYi = sum(bars.map(row => row.buyYi));
const buyDayCount = bars.filter(row => row.buyFlag).length;
const topBuyRows = bars.filter(row => row.buyFlag).sort((a, b) => b.buyYi - a.buyYi).slice(0, 8);
const chiShape = chiSquareShape(bars, shapeKeys);
const kwShape = kruskalShape(bars, shapeKeys);
const adjustedFlag = olsCoefficients(bars, "buyFlag", monthKeys);
const adjustedBuy = olsCoefficients(bars.filter(row => row.buyFlag), "buyYi", monthKeys);
const adjustedStocks = olsCoefficients(bars.filter(row => row.buyFlag), "stockCount", monthKeys);
const adjustedTicket = olsCoefficients(bars.filter(row => row.buyFlag), "ticketYi", monthKeys);

const ret5Q1 = quantile(bars.map(row => row.return5), 1 / 3);
const ret5Q2 = quantile(bars.map(row => row.return5), 2 / 3);
const gapQ1 = quantile(bars.map(row => row.ma20Gap), 1 / 3);
const gapQ2 = quantile(bars.map(row => row.ma20Gap), 2 / 3);
for (const row of bars) {
  row.ret5Bucket = row.return5 <= ret5Q1 ? "近5日偏弱" : row.return5 <= ret5Q2 ? "近5日中性" : "近5日偏强";
  row.gapBucket = row.ma20Gap <= gapQ1 ? "低于MA20/弱" : row.ma20Gap <= gapQ2 ? "MA20附近" : "高于MA20/强";
}
const ret5Stats = ["近5日偏弱", "近5日中性", "近5日偏强"].map(key => summarize(bars, "ret5Bucket", key));
const gapStats = ["低于MA20/弱", "MA20附近", "高于MA20/强"].map(key => summarize(bars, "gapBucket", key));
const boxStats = ["下沿区", "中部区", "上沿区", "箱体外"].map(key => summarize(bars, "boxBucket", key));

const correlationSpecs = [
  ["近1日收益", "return1"],
  ["近5日收益", "return5"],
  ["距MA20", "ma20Gap"],
  ["MA20斜率", "ma20Slope"],
  ["成交额5/20比", "amountRatio"],
  ["距20日高点回撤", "drawdown20"],
  ["全市场龙虎榜股票数", "lhbStocks"],
];
const correlations = correlationSpecs.map(([label, key]) => ({
  label,
  allDays: spearman(bars, key, "buyYi"),
  buyDays: spearman(bars.filter(row => row.buyFlag), key, "buyYi"),
}));

const shapeRuns = [];
for (let start = 0; start < bars.length;) {
  let end = start;
  while (end + 1 < bars.length && bars[end + 1].shape === bars[start].shape) end += 1;
  shapeRuns.push({ start, end, shape: bars[start].shape });
  start = end + 1;
}

function esc(value) {
  return String(value).replace(/[&<>"']/g, char => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&apos;",
  }[char]));
}

function textLines(parts, x, y, options = {}) {
  const size = options.size || 14;
  const color = options.color || "#172B4D";
  const weight = options.weight || 400;
  const anchor = options.anchor || "start";
  const lineHeight = options.lineHeight || size * 1.45;
  return `<text x="${x}" y="${y}" text-anchor="${anchor}" font-family="PingFang SC,Arial,sans-serif" font-size="${size}" font-weight="${weight}" fill="${color}">${parts.map((line, index) => `<tspan x="${x}" dy="${index ? lineHeight : 0}">${esc(line)}</tspan>`).join("")}</text>`;
}

function makeSvg() {
  const W = 2200, H = 1300;
  const left = 90, mainRight = 1530, panelX = 1590, panelW = 540;
  const plotW = mainRight - left;
  const priceTop = 190, priceH = 520, railY = 747, windowRailY = 773;
  const buyTop = 815, buyH = 195, buyBottom = buyTop + buyH;
  const step = plotW / bars.length;
  const minP = Math.min(...bars.map(row => row.low)) * 0.985;
  const maxP = Math.max(...bars.map(row => row.high)) * 1.015;
  const maxBuy = Math.max(...bars.map(row => row.buyYi), 1);
  const x = index => left + (index + 0.5) * step;
  const y = price => priceTop + (maxP - price) * priceH / (maxP - minP);
  const by = value => buyBottom - value / maxBuy * buyH;
  const bodyW = Math.max(2.3, step * 0.58);
  const p = [
    `<svg xmlns="http://www.w3.org/2000/svg" width="${W}" height="${H}" viewBox="0 0 ${W} ${H}">`,
    `<rect width="${W}" height="${H}" fill="#F7F9FC"/>`,
    `<text x="${left}" y="43" font-family="PingFang SC,Arial,sans-serif" font-size="30" font-weight="700" fill="#172B4D">上证指数日K与紫阳东路公开买入环境</text>`,
    `<text x="${left}" y="76" font-family="PingFang SC,Arial,sans-serif" font-size="15" fill="#5E6C84">${fmtDate(bars[0].date)}—${fmtDate(bars.at(-1).date)} · 研究“何时买、买多少”，不评价买后收益</text>`,
  ];

  const kpis = [
    ["交易日", `${bars.length}`],
    ["确切买入日", `${buyDayCount}`],
    ["买入日率", `${(buyDayCount / bars.length * 100).toFixed(1)}%`],
    ["公开买入合计", `${totalBuyYi.toFixed(1)}亿`],
    ["大额日P80", `${p80.toFixed(2)}亿`],
  ];
  kpis.forEach((item, index) => {
    const kx = left + index * 278;
    p.push(`<rect x="${kx}" y="98" width="258" height="66" rx="10" fill="#FFFFFF" stroke="#DDE3EA"/>`);
    p.push(textLines([item[0]], kx + 16, 121, { size: 12, color: "#6B778C" }));
    p.push(textLines([item[1]], kx + 16, 151, { size: 24, color: "#172B4D", weight: 700 }));
  });

  for (const run of shapeRuns) {
    const xx = x(run.start) - step / 2;
    const width = x(run.end) - x(run.start) + step;
    p.push(`<rect x="${xx}" y="${priceTop}" width="${width}" height="${priceH}" fill="${shapeFill[run.shape] || "#FFFFFF"}"/>`);
    p.push(`<rect x="${xx}" y="${buyTop}" width="${width}" height="${buyH}" fill="${shapeFill[run.shape] || "#FFFFFF"}"/>`);
    if (width > 55) p.push(textLines([shapeLabel[run.shape] || run.shape], xx + 8, priceTop + 20, { size: 11, color: shapeColor[run.shape] || "#6B778C", weight: 600 }));
  }
  for (let k = 0; k <= 6; k++) {
    const price = minP + (maxP - minP) * k / 6;
    const yy = y(price);
    p.push(`<line x1="${left}" y1="${yy}" x2="${mainRight}" y2="${yy}" stroke="#DDE3EA" stroke-width="1"/>`);
    p.push(textLines([price.toFixed(0)], left - 12, yy + 4, { size: 12, color: "#6B778C", anchor: "end" }));
  }
  p.push(textLines(["上证指数日K · 背景为指数形态 · 深蓝线为MA20"], left, priceTop - 14, { size: 13, color: "#5E6C84", weight: 600 }));

  for (const box of priceBoxes) {
    const xx = x(box.start) - step / 2;
    const width = x(box.end) - x(box.start) + step;
    p.push(`<rect x="${xx}" y="${y(box.high)}" width="${width}" height="${Math.max(4, y(box.low) - y(box.high))}" fill="none" stroke="#1F6FEB" stroke-width="2" stroke-dasharray="8 5"/>`);
    p.push(textLines([`箱体${box.n}  ${box.low.toFixed(0)}–${box.high.toFixed(0)}`], xx + 6, y(box.high) - 7, { size: 11, color: "#0D47A1", weight: 650 }));
  }

  bars.forEach((row, index) => {
    const up = row.close >= row.open;
    const color = up ? "#D64545" : "#1F9D72";
    p.push(`<line x1="${x(index)}" y1="${y(row.high)}" x2="${x(index)}" y2="${y(row.low)}" stroke="${color}" stroke-width="1.1"/>`);
    const yy = Math.min(y(row.open), y(row.close));
    const height = Math.max(1.2, Math.abs(y(row.open) - y(row.close)));
    p.push(`<rect x="${x(index) - bodyW / 2}" y="${yy}" width="${bodyW}" height="${height}" fill="${up ? color : shapeFill[row.shape]}" stroke="${color}" stroke-width="1"/>`);
  });
  p.push(`<polyline fill="none" stroke="#172B4D" stroke-width="2.2" points="${bars.map((row, index) => `${x(index)},${y(row.ma20)}`).join(" ")}"/>`);

  p.push(`<line x1="${left}" y1="${railY}" x2="${mainRight}" y2="${railY}" stroke="#C9D1D9"/>`);
  p.push(`<line x1="${left}" y1="${windowRailY}" x2="${mainRight}" y2="${windowRailY}" stroke="#E3E7EC"/>`);
  p.push(textLines(["确切买入"], left, railY - 10, { size: 11, color: "#8E1C62", weight: 600 }));
  p.push(textLines(["窗口锚点"], left, windowRailY + 18, { size: 10, color: "#B75B00" }));
  for (const row of bars.filter(item => item.buyFlag)) {
    const radius = 3.5 + 9 * Math.sqrt(row.buyYi / maxBuy);
    p.push(`<circle cx="${x(row.i)}" cy="${railY}" r="${radius}" fill="#B52A74" fill-opacity=".82" stroke="${shapeColor[row.shape]}" stroke-width="2"/>`);
  }
  for (const row of bars.filter(item => item.windowBuy > 0)) {
    const xx = x(row.i), size = 4.5;
    p.push(`<polygon points="${xx},${windowRailY - size} ${xx + size},${windowRailY} ${xx},${windowRailY + size} ${xx - size},${windowRailY}" fill="#FFFFFF" stroke="#EF6C00" stroke-width="1.6"/>`);
  }

  p.push(textLines(["紫阳东路确切单日公开买入额（亿元）· 柱色对应指数形态"], left, buyTop - 14, { size: 13, color: "#5E6C84", weight: 600 }));
  for (let k = 0; k <= 3; k++) {
    const value = maxBuy * k / 3;
    const yy = by(value);
    p.push(`<line x1="${left}" y1="${yy}" x2="${mainRight}" y2="${yy}" stroke="#DDE3EA" stroke-width="1"/>`);
    p.push(textLines([value.toFixed(1)], left - 12, yy + 4, { size: 11, color: "#6B778C", anchor: "end" }));
  }
  for (const row of bars.filter(item => item.buyFlag)) {
    const yy = by(row.buyYi);
    p.push(`<rect x="${x(row.i) - Math.max(2, bodyW / 2)}" y="${yy}" width="${Math.max(4, bodyW)}" height="${buyBottom - yy}" fill="${shapeColor[row.shape]}" fill-opacity=".78"/>`);
  }
  const p80Y = by(p80);
  p.push(`<line x1="${left}" y1="${p80Y}" x2="${mainRight}" y2="${p80Y}" stroke="#8E1C62" stroke-width="1.6" stroke-dasharray="7 5"/>`);
  p.push(textLines([`P80 ${p80.toFixed(2)}亿`], mainRight - 5, p80Y - 6, { size: 11, color: "#8E1C62", anchor: "end", weight: 600 }));
  topBuyRows.forEach((row, index) => {
    const labelY = Math.max(buyTop + 14, by(row.buyYi) - 9 - (index % 2) * 16);
    p.push(textLines([`${fmtDate(row.date).slice(5)}  ${row.buyYi.toFixed(1)}亿/${row.stockCount}股`], x(row.i), labelY, { size: 9.5, color: "#6A174B", anchor: "middle", weight: 650 }));
  });
  for (let i = 0; i < bars.length; i += 16) {
    p.push(textLines([fmtDate(bars[i].date).slice(5)], x(i), buyBottom + 25, { size: 11, color: "#6B778C", anchor: "middle" }));
  }

  // 右侧：结论与环境对比。
  p.push(`<rect x="${panelX}" y="98" width="${panelW}" height="152" rx="12" fill="#172B4D"/>`);
  p.push(textLines(["读图结论"], panelX + 22, 128, { size: 15, color: "#FFFFFF", weight: 700 }));
  p.push(textLines([
    "买不买：月份活动阶段强于指数形态。",
    "买多少：上行偏单股金额，下行偏覆盖股票数。",
    "形态是次级条件，不能解释为单独触发器。",
  ], panelX + 22, 158, { size: 14, color: "#FFFFFF", lineHeight: 27 }));

  p.push(`<rect x="${panelX}" y="270" width="${panelW}" height="230" rx="12" fill="#FFFFFF" stroke="#DDE3EA"/>`);
  p.push(textLines(["买入日率｜是否执行买入"], panelX + 20, 301, { size: 15, weight: 700 }));
  const rateBaseX = panelX + 112, rateMaxW = 365;
  shapeStats.forEach((stat, index) => {
    const yy = 340 + index * 48;
    p.push(textLines([shapeLabel[stat.key]], rateBaseX - 18, yy + 5, { size: 12, color: shapeColor[stat.key], anchor: "end", weight: 650 }));
    p.push(`<rect x="${rateBaseX}" y="${yy - 10}" width="${rateMaxW}" height="18" rx="9" fill="#EDF0F4"/>`);
    p.push(`<rect x="${rateBaseX}" y="${yy - 10}" width="${rateMaxW * stat.rate}" height="18" rx="9" fill="${shapeColor[stat.key]}"/>`);
    p.push(textLines([`${(stat.rate * 100).toFixed(1)}%  (${stat.buyDays}/${stat.days})`], rateBaseX + rateMaxW, yy + 5, { size: 11, color: "#172B4D", anchor: "end", weight: 650 }));
  });
  p.push(textLines([
    `形态关联较弱：Cramér V=${chiShape.cramerV.toFixed(2)}，p=${chiShape.p.toFixed(2)}`,
    `控制月份后：上行${adjustedFlag[1] >= 0 ? "+" : ""}${(adjustedFlag[1] * 100).toFixed(0)}pct；下行${adjustedFlag[2] >= 0 ? "+" : ""}${(adjustedFlag[2] * 100).toFixed(0)}pct（相对震荡）`,
  ], panelX + 20, 468, { size: 11, color: "#5E6C84", lineHeight: 18 }));

  p.push(`<rect x="${panelX}" y="520" width="${panelW}" height="245" rx="12" fill="#FFFFFF" stroke="#DDE3EA"/>`);
  p.push(textLines(["有买入时｜力度与构成"], panelX + 20, 551, { size: 15, weight: 700 }));
  const amountBaseY = 700, amountMaxH = 105, amountScale = 4.3;
  shapeStats.forEach((stat, index) => {
    const xx = panelX + 100 + index * 150;
    const height = amountMaxH * stat.avgBuyDayYi / amountScale;
    p.push(`<rect x="${xx - 27}" y="${amountBaseY - height}" width="54" height="${height}" rx="5" fill="${shapeColor[stat.key]}" fill-opacity=".82"/>`);
    const medianY = amountBaseY - amountMaxH * stat.medianBuyDayYi / amountScale;
    p.push(`<line x1="${xx - 34}" y1="${medianY}" x2="${xx + 34}" y2="${medianY}" stroke="#172B4D" stroke-width="2"/>`);
    p.push(textLines([`${stat.avgBuyDayYi.toFixed(2)}亿`], xx, amountBaseY - height - 8, { size: 11, color: shapeColor[stat.key], anchor: "middle", weight: 700 }));
    p.push(textLines([shapeLabel[stat.key]], xx, amountBaseY + 19, { size: 12, anchor: "middle", weight: 650 }));
    p.push(textLines([`${stat.meanStocks.toFixed(2)}股/日 × ${stat.aggregateTicketYi.toFixed(2)}亿/股`], xx, amountBaseY + 38, { size: 9.5, color: "#5E6C84", anchor: "middle" }));
  });
  p.push(textLines([`中位数=黑线；形态间力度分布差异 p=${kwShape.p.toFixed(2)}`], panelX + 20, 750, { size: 11, color: "#5E6C84" }));

  p.push(`<rect x="${panelX}" y="785" width="${panelW}" height="280" rx="12" fill="#FFFFFF" stroke="#DDE3EA"/>`);
  p.push(textLines(["月份活动阶段｜比形态差异更大"], panelX + 20, 816, { size: 15, weight: 700 }));
  const monthBaseY = 1005, monthMaxH = 145, monthStep = 67;
  monthStats.forEach((stat, index) => {
    const xx = panelX + 48 + index * monthStep;
    const height = monthMaxH * stat.rate;
    p.push(`<rect x="${xx - 18}" y="${monthBaseY - height}" width="36" height="${height}" rx="4" fill="#8E1C62" fill-opacity=".78"/>`);
    p.push(textLines([`${(stat.rate * 100).toFixed(0)}%`], xx, monthBaseY - height - 7, { size: 10, color: "#6A174B", anchor: "middle", weight: 650 }));
    p.push(textLines([fmtMonth(stat.key).slice(5)], xx, monthBaseY + 18, { size: 10, color: "#5E6C84", anchor: "middle" }));
  });
  p.push(textLines([
    "1月40% → 2—3月100% → 7月39%",
    "说明样本内存在明显的营业部活动阶段/披露覆盖变化。",
  ], panelX + 20, 1040, { size: 11, color: "#5E6C84", lineHeight: 18 }));

  p.push(`<rect x="${panelX}" y="1085" width="${panelW}" height="150" rx="12" fill="#FFF7E6" stroke="#F0C36D"/>`);
  p.push(textLines(["解释边界"], panelX + 20, 1116, { size: 14, color: "#6B5200", weight: 700 }));
  p.push(textLines([
    "公开龙虎榜是条件披露，不是营业部全部交易。",
    "WINDOW_BUY仅作橙色锚点，不进入行为统计。",
    "关联不等于因果，也不构成指数交易建议。",
  ], panelX + 20, 1145, { size: 12, color: "#6B5200", lineHeight: 23 }));

  p.push(textLines([
    `圆点面积=确切买入额；边框/柱色=指数形态。全市场龙虎榜股票数与买入额Spearman：全样本${spearman(bars, "lhbStocks", "buyYi").toFixed(2)}，仅买入日${spearman(bars.filter(row => row.buyFlag), "lhbStocks", "buyYi").toFixed(2)}。`,
  ], left, 1270, { size: 11, color: "#8993A4" }));
  p.push("</svg>");
  return p.join("");
}

const svg = makeSvg();
await fs.writeFile(svgPath, svg, "utf8");
await sharp(Buffer.from(svg)).png().toFile(pngPath);
const pngBase64 = (await fs.readFile(pngPath)).toString("base64");

const workbook = Workbook.create();
const trend = workbook.worksheets.add("趋势与环境");
const analysis = workbook.worksheets.add("环境分析");
const daily = workbook.worksheets.add("每日样本");
const eventSheet = workbook.worksheets.add("紫阳披露明细");
const boxSheet = workbook.worksheets.add("价格箱体");
const methods = workbook.worksheets.add("方法与边界");
for (const sheet of workbook.worksheets.items) sheet.showGridLines = false;

const colors = {
  navy: "#172B4D", blue: "#1976D2", lightBlue: "#E9F2FF", gold: "#FFF3CD",
  rose: "#8E1C62", paleRose: "#FCE8F3", gray: "#F4F5F7", green: "#1F9D72",
};

function title(sheet, range, value) {
  sheet.getRange(range).merge();
  sheet.getRange(range.split(":")[0]).values = [[value]];
  sheet.getRange(range).format = {
    fill: colors.navy,
    font: { bold: true, color: "#FFFFFF", size: 17 },
    rowHeight: 34,
    verticalAlignment: "center",
  };
}

function header(sheet, range) {
  sheet.getRange(range).format = {
    fill: colors.blue,
    font: { bold: true, color: "#FFFFFF" },
    rowHeight: 24,
    verticalAlignment: "center",
  };
}

title(trend, "A1:Q1", "上证指数｜紫阳东路公开买入环境分析（优化版）");
trend.getRange("A2:Q2").merge();
trend.getRange("A2").values = [[`核心问题：紫阳东路在什么市场环境下执行买入，买入力度是否与指数形态有关？样本 ${fmtDate(bars[0].date)}—${fmtDate(bars.at(-1).date)}。`]];
trend.getRange("A2:Q2").format = { fill: colors.lightBlue, font: { color: colors.navy }, rowHeight: 25 };
trend.getRange("A3:Q3").merge();
trend.getRange("A3").values = [["结论：指数形态是次级条件；样本内更强的是月份活动阶段。上行日的大额来自较高单股金额，下行日则更多来自覆盖股票数。"]];
trend.getRange("A3:Q3").format = { fill: colors.gold, font: { color: "#6B5200", bold: true }, rowHeight: 28 };
trend.images.add({
  dataUrl: `data:image/png;base64,${pngBase64}`,
  anchor: { from: { row: 3, col: 0 }, extent: { widthPx: 1660, heightPx: 980 } },
});
for (let col = 0; col < 17; col++) trend.getRangeByIndexes(0, col, 1, 1).format.columnWidthPx = 98;
for (let row = 3; row < 53; row++) trend.getRangeByIndexes(row, 0, 1, 1).format.rowHeightPx = 20;
trend.freezePanes.freezeRows(3);

title(analysis, "A1:Q1", "环境分析｜买入发生率、条件力度与活动阶段");
analysis.getRange("A2:Q2").merge();
analysis.getRange("A2").values = [["先看“是否买入”，再看“有买入时买多少”；原始累计金额不能直接比较，因为不同形态持续天数不同。"]];
analysis.getRange("A2:Q2").format = { fill: colors.lightBlue, font: { color: colors.navy }, rowHeight: 25 };

analysis.getRange("A4:J4").values = [["指数形态","交易日","买入日","买入日率","合计买入(亿)","每交易日(亿)","每买入日均值(亿)","每买入日中位数(亿)","平均股票数/买入日","单股平均额(亿)"]];
header(analysis, "A4:J4");
analysis.getRange("A5:J7").values = shapeStats.map(stat => [
  shapeLabel[stat.key], stat.days, stat.buyDays, stat.rate, stat.totalYi, stat.perDayYi,
  stat.avgBuyDayYi, stat.medianBuyDayYi, stat.meanStocks, stat.aggregateTicketYi,
]);
analysis.getRange("D5:D7").format.numberFormat = "0.0%";
analysis.getRange("E5:J7").format.numberFormat = "0.00";

analysis.getRange("A10:H10").values = [["月份","交易日","买入日","买入日率","合计买入(亿)","每买入日均值(亿)","全市场龙虎榜股票数/日","阶段判断"]];
header(analysis, "A10:H10");
analysis.getRange(`A11:H${10 + monthStats.length}`).values = monthStats.map(stat => [
  fmtMonth(stat.key), stat.days, stat.buyDays, stat.rate, stat.totalYi, stat.avgBuyDayYi, stat.avgLhbStocks,
  stat.rate >= 0.95 ? "高活跃" : stat.rate >= 0.7 ? "活跃" : "低活跃",
]);
analysis.getRange(`D11:D${10 + monthStats.length}`).format.numberFormat = "0.0%";
analysis.getRange(`E11:G${10 + monthStats.length}`).format.numberFormat = "0.00";

analysis.getRange("A21:I21").values = [["距MA20分组","交易日","买入日","买入日率","每交易日买入(亿)","每买入日均值(亿)","每买入日中位数(亿)","平均股票数/买入日","单股平均额(亿)"]];
header(analysis, "A21:I21");
analysis.getRange("A22:I24").values = gapStats.map(stat => [
  stat.key, stat.days, stat.buyDays, stat.rate, stat.perDayYi, stat.avgBuyDayYi,
  stat.medianBuyDayYi, stat.meanStocks, stat.aggregateTicketYi,
]);
analysis.getRange("D22:D24").format.numberFormat = "0.0%";
analysis.getRange("E22:I24").format.numberFormat = "0.00";

analysis.getRange("A27:I27").values = [["近5日指数强弱","交易日","买入日","买入日率","每交易日买入(亿)","每买入日均值(亿)","每买入日中位数(亿)","平均股票数/买入日","单股平均额(亿)"]];
header(analysis, "A27:I27");
analysis.getRange("A28:I30").values = ret5Stats.map(stat => [
  stat.key, stat.days, stat.buyDays, stat.rate, stat.perDayYi, stat.avgBuyDayYi,
  stat.medianBuyDayYi, stat.meanStocks, stat.aggregateTicketYi,
]);
analysis.getRange("D28:D30").format.numberFormat = "0.0%";
analysis.getRange("E28:I30").format.numberFormat = "0.00";

analysis.getRange("L4:Q4").values = [["箱体位置","交易日","买入日","买入日率","每交易日买入(亿)","每买入日均值(亿)"]];
header(analysis, "L4:Q4");
analysis.getRange("L5:Q8").values = boxStats.map(stat => [
  stat.key, stat.days, stat.buyDays, stat.rate, stat.perDayYi, stat.avgBuyDayYi,
]);
analysis.getRange("O5:O8").format.numberFormat = "0.0%";
analysis.getRange("P5:Q8").format.numberFormat = "0.00";

analysis.getRange("L11:N11").values = [["市场变量","买入额相关ρ（含零值日）","买入额相关ρ（仅买入日）"]];
header(analysis, "L11:N11");
analysis.getRange(`L12:N${11 + correlations.length}`).values = correlations.map(row => [row.label, row.allDays, row.buyDays]);
analysis.getRange(`M12:N${11 + correlations.length}`).format.numberFormat = "0.00";

analysis.getRange("L21:Q21").values = [["比较项","原始样本","时间控制后","效应/检验","p值","解释"]];
header(analysis, "L21:Q21");
analysis.getRange("L22:Q25").values = [
  ["上行买入日率 vs 震荡", shapeStats[0].rate - shapeStats[1].rate, adjustedFlag[1], "月份固定效应描述", null, "原始差异大幅收敛"],
  ["下行买入日率 vs 震荡", shapeStats[2].rate - shapeStats[1].rate, adjustedFlag[2], "月份固定效应描述", null, "形态不是主要开关"],
  ["形态 × 是否买入", null, null, `Cramér V=${chiShape.cramerV.toFixed(3)}`, chiShape.p, "弱关联"],
  ["形态 × 条件买入额", null, null, `Kruskal H=${kwShape.h.toFixed(3)}`, kwShape.p, "差异证据不足"],
];
analysis.getRange("M22:N23").format.numberFormat = "0.0%";
analysis.getRange("P22:P25").format.numberFormat = "0.000";

analysis.getRange("L28:Q28").values = [["形态","买入额调整差(亿)","股票数调整差","单股额调整差(亿)","力度构成","解释"]];
header(analysis, "L28:Q28");
analysis.getRange("L29:Q31").values = [
  ["上行 vs 震荡", adjustedBuy[1], adjustedStocks[1], adjustedTicket[1], "少一些股票、单股更大", "大额日偏单票金额"],
  ["下行 vs 震荡", adjustedBuy[2], adjustedStocks[2], adjustedTicket[2], "更多股票、单股略小", "大额日偏覆盖广度"],
  ["震荡（基准）", 0, 0, 0, "基准", "不作因果解释"],
];
analysis.getRange("M29:O31").format.numberFormat = "0.00";

for (const range of ["A5:J7", `A11:H${10 + monthStats.length}`, "A22:I24", "A28:I30", "L5:Q8", `L12:N${11 + correlations.length}`, "L22:Q25", "L29:Q31"]) {
  analysis.getRange(range).format.rowHeight = 22;
}
analysis.getRange("A:Q").format.wrapText = true;
analysis.getRange("A:A").format.columnWidth = 18;
for (const col of ["B","C","D","E","F","G","H","I","J","M","N","O","P"]) analysis.getRange(`${col}:${col}`).format.columnWidth = 15;
analysis.getRange("L:L").format.columnWidth = 24;
analysis.getRange("Q:Q").format.columnWidth = 25;
analysis.freezePanes.freezeRows(4);

title(daily, "A1:AA1", "每日样本｜上证指数环境与紫阳东路公开买入");
daily.getRange("A2:AA2").merge();
daily.getRange("A2").values = [["所有交易日均保留；没有确切买入的日期按0计入“是否买入”分析。黄色列为公式派生字段。"]];
daily.getRange("A2:AA2").format = { fill: colors.gold, font: { color: "#6B5200" }, rowHeight: 24 };
daily.getRange("A3:AA3").values = [[
  "日期","开盘","最高","最低","收盘","涨跌幅%","近1日收益","近5日收益","MA20","MA20斜率",
  "成交额5/20比","指数形态","确切买入额(亿)","事件数","股票数","龙虎榜股票数","买入日标记",
  "单股平均额(亿)","距MA20","距20日高点回撤","箱体","箱体下沿","箱体上沿","箱体位置","箱体区域",
  "月份","累计窗口锚点额(亿)",
]];
header(daily, "A3:AA3");
const dailyEnd = 3 + bars.length;
daily.getRange(`A4:P${dailyEnd}`).values = bars.map(row => [
  row.date, row.open, row.high, row.low, row.close, row.pct, row.return1, row.return5, row.ma20, row.ma20Slope,
  row.amountRatio, shapeLabel[row.shape], row.buyYi, row.eventCount, row.stockCount, row.lhbStocks,
]);
daily.getRange(`T4:W${dailyEnd}`).values = bars.map(row => [
  row.drawdown20, row.boxNo, row.boxLow, row.boxHigh,
]);
daily.getRange(`Z4:AA${dailyEnd}`).values = bars.map(row => [fmtMonth(row.month), row.windowBuy / 1e8]);
daily.getRange(`Q4:Q${dailyEnd}`).formulas = bars.map((_, index) => [`=IF(M${index + 4}>0,1,0)`]);
daily.getRange(`R4:R${dailyEnd}`).formulas = bars.map((_, index) => [`=IF(O${index + 4}>0,M${index + 4}/O${index + 4},"")`]);
daily.getRange(`S4:S${dailyEnd}`).formulas = bars.map((_, index) => [`=IFERROR(E${index + 4}/I${index + 4}-1,"")`]);
daily.getRange(`X4:X${dailyEnd}`).formulas = bars.map((row, index) => [
  row.boxNo ? `=IFERROR((E${index + 4}-V${index + 4})/(W${index + 4}-V${index + 4}),"")` : '=""',
]);
daily.getRange(`Y4:Y${dailyEnd}`).formulas = bars.map((_, index) => [
  `=IF(X${index + 4}="","箱体外",IF(X${index + 4}<1/3,"下沿区",IF(X${index + 4}<2/3,"中部区","上沿区")))`,
]);
daily.getRange(`A4:A${dailyEnd}`).format.numberFormat = "yyyy-mm-dd";
daily.getRange(`B4:F${dailyEnd}`).format.numberFormat = "0.00";
daily.getRange(`G4:H${dailyEnd}`).format.numberFormat = "0.00%";
daily.getRange(`I4:I${dailyEnd}`).format.numberFormat = "0.00";
daily.getRange(`J4:K${dailyEnd}`).format.numberFormat = "0.00%";
daily.getRange(`M4:R${dailyEnd}`).format.numberFormat = "0.00";
daily.getRange(`S4:T${dailyEnd}`).format.numberFormat = "0.00%";
daily.getRange(`V4:W${dailyEnd}`).format.numberFormat = "0.00";
daily.getRange(`X4:X${dailyEnd}`).format.numberFormat = "0.0%";
daily.getRange(`AA4:AA${dailyEnd}`).format.numberFormat = "0.00";
for (const col of ["Q","R","S","X","Y"]) daily.getRange(`${col}4:${col}${dailyEnd}`).format.fill = "#FFFBEA";
daily.getRange("A:AA").format.autofitColumns();
for (const col of ["A","L","Y","Z"]) daily.getRange(`${col}:${col}`).format.columnWidth = 16;
daily.freezePanes.freezeRows(3);

title(eventSheet, "A1:H1", "紫阳东路公开披露明细");
eventSheet.getRange("A2:H2").merge();
eventSheet.getRange("A2").values = [["EXACT_BUY进入核心行为分析；WINDOW_BUY仅保留为累计披露锚点。"]];
eventSheet.getRange("A2:H2").format = { fill: colors.gold, font: { color: "#6B5200" }, rowHeight: 24 };
eventSheet.getRange("A3:H3").values = [["日期","披露类型","股票代码","股票名称","买入额(亿)","指数形态","近5日收益","披露原因"]];
header(eventSheet, "A3:H3");
const eventRows = payload.events
  .filter(row => d8(row.anchor_date) >= "20260101")
  .sort((a, b) => d8(a.anchor_date).localeCompare(d8(b.anchor_date)) || String(a.ts_code).localeCompare(String(b.ts_code)))
  .map(row => {
    const bar = bars[dateIndex.get(d8(row.anchor_date))];
    return [
      dateOf(row.anchor_date), row.event_type, row.ts_code, row.stock_name || "",
      +row.buy_amount / 1e8, bar?.shape ? shapeLabel[bar.shape] : "", bar?.return5 ?? null, row.reasons || "",
    ];
  });
const eventEnd = 3 + eventRows.length;
eventSheet.getRange(`A4:H${eventEnd}`).values = eventRows;
eventSheet.getRange(`A4:A${eventEnd}`).format.numberFormat = "yyyy-mm-dd";
eventSheet.getRange(`E4:E${eventEnd}`).format.numberFormat = "0.00";
eventSheet.getRange(`G4:G${eventEnd}`).format.numberFormat = "0.00%";
eventSheet.getRange("A:H").format.autofitColumns();
eventSheet.getRange("H:H").format.columnWidth = 52;
eventSheet.getRange(`H4:H${eventEnd}`).format.wrapText = true;
eventSheet.freezePanes.freezeRows(3);

title(boxSheet, "A1:J1", "价格箱体与箱体位置买入统计");
boxSheet.getRange("A3:J3").values = [["箱体","开始日期","结束日期","交易日","下沿","上沿","振幅","买入日率","每买入日均额(亿)","突破后状态"]];
header(boxSheet, "A3:J3");
boxSheet.getRange(`A4:J${3 + priceBoxes.length}`).values = priceBoxes.map(box => {
  const rows = bars.slice(box.start, box.end + 1);
  const buyRows = rows.filter(row => row.buyFlag);
  const next = bars[box.end + 1];
  const status = !next ? "样本结束" : next.high > box.high ? "向上突破" : next.low < box.low ? "向下突破" : "离开箱体";
  return [
    `箱体${box.n}`, bars[box.start].date, bars[box.end].date, rows.length, box.low, box.high,
    box.widthRatio, safeDiv(buyRows.length, rows.length), mean(buyRows.map(row => row.buyYi)), status,
  ];
});
boxSheet.getRange(`B4:C${3 + priceBoxes.length}`).format.numberFormat = "yyyy-mm-dd";
boxSheet.getRange(`E4:F${3 + priceBoxes.length}`).format.numberFormat = "0.00";
boxSheet.getRange(`G4:H${3 + priceBoxes.length}`).format.numberFormat = "0.0%";
boxSheet.getRange(`I4:I${3 + priceBoxes.length}`).format.numberFormat = "0.00";
boxSheet.getRange("A:J").format.autofitColumns();
boxSheet.freezePanes.freezeRows(3);

title(methods, "A1:D1", "方法、口径与解释边界");
methods.getRange("A3:D3").values = [["项目","定义","计算/来源","解释边界"]];
header(methods, "A3:D3");
const methodRows = [
  ["研究问题","分析什么环境下出现公开买入，以及有买入时力度是否与指数形态有关。","交易日为样本单位；先二元发生、后条件力度。","不分析买后收益，不把披露事件当成指数买入信号。"],
  ["研究区间",`${fmtDate(bars[0].date)}至${fmtDate(bars.at(-1).date)}，共${bars.length}个交易日。`,"上证指数000001.SH日线。","2026-01-01为节假日，首个交易日为2026-01-05。"],
  ["确切买入","同日披露窗口、营业部位于买入前五且买入额大于0。","EXACT_BUY；无事件交易日补0。","公开龙虎榜不等于营业部全部成交。"],
  ["累计窗口","多日累计记录以period_end作披露锚点。","WINDOW_BUY仅画橙色空心菱形。","不进入发生率、金额和相关性统计。"],
  ["指数形态","上行：收盘>MA20、MA20五日斜率>0、近5日收益>0；下行条件对称；其余为震荡。","使用当日收盘后可得的指数日线特征。","这是描述性分类，不是因果变量。"],
  ["买入力度","当日确切买入总额；拆为股票数与单股平均金额。","总额=覆盖股票数×单股平均额。","总额受事件广度与单票金额共同影响。"],
  ["价格箱体","从左向右扩展；最高—最低振幅≤中位价6%，且至少10个交易日。","原始日K高低价；突破后重启识别。","箱体外仅${boxStats.at(-1).days}日，结论不宜外推。"],
  ["形态关联检验",`是否买入：Cramér V=${chiShape.cramerV.toFixed(3)}，p=${chiShape.p.toFixed(3)}；条件金额：Kruskal H=${kwShape.h.toFixed(3)}，p=${kwShape.p.toFixed(3)}。`,"三种形态分组的描述性检验。","样本不支持“形态单独决定买入”的强结论。"],
  ["月份控制","线性概率/线性模型加入月份虚拟变量；震荡为基准。","用于分离指数形态与营业部活动阶段。","仅描述样本内调整差异，不是因果估计。"],
  ["龙虎榜机会集","每日全市场龙虎榜股票数与席位记录来自Tushare top_inst。","用于检查买入力度是否只是随披露机会数量扩大。","披露规则及市场结构变化可能造成选择偏差。"],
  ["样本质量",`原始龙虎榜121,057行；紫阳去重事件366个，其中确切235、窗口131；确切披露日${buyDayCount}天。`,"事件与指数日期匹配后缺失0。","数据截至2026-07-24。"],
  ["免责声明","本工作簿为公开数据行为分析。","不构成证券投资建议。","关联不等于因果。"],
];
methods.getRange(`A4:D${3 + methodRows.length}`).values = methodRows;
methods.getRange("A:D").format.wrapText = true;
methods.getRange("A:A").format.columnWidth = 19;
methods.getRange("B:B").format.columnWidth = 54;
methods.getRange("C:C").format.columnWidth = 48;
methods.getRange("D:D").format.columnWidth = 48;
methods.getRange(`A4:D${3 + methodRows.length}`).format.rowHeight = 58;
methods.freezePanes.freezeRows(3);

workbook.comments.setSelf({ displayName: "Codex" });
workbook.comments.addThread(
  { cell: methods.getRange("B6") },
  "核心统计只使用EXACT_BUY。多日累计窗口的期末锚点并不是确切成交日，因此仅在趋势图中保留为辅助标记。",
);

const inspectAnalysis = await workbook.inspect({
  kind: "table",
  range: "环境分析!A1:Q31",
  include: "values,formulas",
  tableMaxRows: 40,
  tableMaxCols: 17,
  maxChars: 20000,
});
console.log(inspectAnalysis.ndjson);
const inspectDaily = await workbook.inspect({
  kind: "table",
  range: "每日样本!A1:AA12",
  include: "values,formulas",
  tableMaxRows: 12,
  tableMaxCols: 27,
  maxChars: 12000,
});
console.log(inspectDaily.ndjson);
const errors = await workbook.inspect({
  kind: "match",
  searchTerm: "#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A",
  options: { useRegex: true, maxResults: 300 },
  summary: "final formula error scan",
});
console.log(errors.ndjson);

const out = await SpreadsheetFile.exportXlsx(workbook);
await out.save(outputPath);
const rendered = await workbook.render({ sheetName: "趋势与环境", range: "A1:Q53", scale: 1, format: "png" });
await fs.writeFile(previewPath, new Uint8Array(await rendered.arrayBuffer()));

console.log(JSON.stringify({
  outputPath,
  pngPath,
  svgPath,
  previewPath,
  bars: bars.length,
  exactBuyDays: buyDayCount,
  totalBuyYi: round(totalBuyYi, 2),
  p80: round(p80, 2),
  shapeStats: shapeStats.map(stat => ({
    shape: stat.key,
    days: stat.days,
    buyDays: stat.buyDays,
    buyRate: round(stat.rate, 4),
    avgBuyDayYi: round(stat.avgBuyDayYi, 4),
  })),
  monthAdjustedRateDifference: { upVsRange: round(adjustedFlag[1], 4), downVsRange: round(adjustedFlag[2], 4) },
  chiShape: { cramerV: round(chiShape.cramerV, 4), p: round(chiShape.p, 4) },
  kwShape: { h: round(kwShape.h, 4), p: round(kwShape.p, 4) },
}, null, 2));
