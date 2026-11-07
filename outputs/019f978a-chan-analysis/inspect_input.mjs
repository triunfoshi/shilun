import { FileBlob, SpreadsheetFile } from "@oai/artifact-tool";

const inputPath = "/Users/shibicheng/Desktop/K线导出_600584_日线数据.xlsx";
const input = await FileBlob.load(inputPath);
const workbook = await SpreadsheetFile.importXlsx(input);

const summary = await workbook.inspect({
  kind: "workbook,sheet,table,drawing",
  maxChars: 12000,
  tableMaxRows: 12,
  tableMaxCols: 16,
  tableMaxCellChars: 120,
});
console.log(summary.ndjson);

const sheets = await workbook.inspect({ kind: "sheet", include: "id,name", maxChars: 3000 });
console.log(sheets.ndjson);

const preview = await workbook.render({
  sheetName: workbook.worksheets.getItemAt(0).name,
  autoCrop: "all",
  scale: 1.5,
  format: "png",
});
await preview.save("/Users/shibicheng/shilun_standalone/outputs/019f978a-chan-analysis/input_preview.png");
