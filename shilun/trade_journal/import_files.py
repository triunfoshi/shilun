"""Bounded, non-executing source adapters; preview/import remain the owners.

Original bytes are kept privately and recoverably. Inspection masks account
values. Mappings and excluded rows/sheets are explicit; unresolved input never
produces a partially importable payload. This module performs no DB writes.
"""
from __future__ import annotations

import base64
from copy import deepcopy
import csv
from datetime import date, datetime, time, timedelta
from decimal import Decimal, InvalidOperation
import hashlib
from io import BytesIO, StringIO
import math
from pathlib import PurePath
import re
import struct
from xml.etree import ElementTree
import zipfile

from bson import BSON

from .ledger import digest
from .service import TradeJournalError

VERSION = "eastmoney-file-adapter-v1"
LIMITS = {"max_file_bytes": 8*1024*1024, "max_uncompressed_bytes": 64*1024*1024,
          "max_zip_members": 2000, "max_rows": 50000, "max_columns": 100,
          "max_sheets": 30, "max_cells": 500000, "max_cell_characters": 16000,
          "max_source_bson_bytes":14*1024*1024, "max_mapping_bytes":2*1024*1024}
LABELS = {
    "date": ("交易/发生日期", "date", ("发生日期", "成交日期", "交易日期", "日期")),
    "settlement_date": ("交收日期", "date", ("交收日期", "交割日期")),
    "execution_time": ("真实成交时间", "time", ("成交时间",)),
    "statement_event_time": ("交割发生时间", "time", ("发生时间",)),
    "time": ("时间（需选择语义）", "time", ("时间",)),
    "code": ("证券代码", "identifier", ("证券代码", "股票代码", "代码")),
    "name": ("证券名称", "text", ("证券名称", "股票名称", "名称")),
    "kind": ("买卖/业务类别", "kind", ("买卖类别", "委托方向", "操作", "业务名称", "买卖方向")),
    "quantity": ("数量（股）", "integer", ("成交数量", "发生数量", "数量")),
    "price": ("成交价格（元）", "number", ("成交价格", "成交均价", "价格")),
    "amount": ("成交金额（元）", "number", ("成交金额",)),
    "net_cash": ("净收付/发生金额（元）", "number", ("发生金额", "清算金额", "交割金额", "净收付", "净现金流")),
    "fees": ("费用合计（元）", "number", ("费用合计", "手续费", "总费用")),
    "commission": ("佣金", "number", ("佣金",)),
    "regulatory_fee": ("交易规费", "number", ("交易规费", "规费")),
    "stamp_tax": ("印花税", "number", ("印花税",)),
    "transfer_fee": ("过户费", "number", ("过户费",)),
    "quantity_after": ("股票/股份余额", "integer", ("股票余额", "股份余额", "证券余额")),
    "broker_execution_id": ("券商成交编号", "identifier", ("成交编号", "成交序号")),
    "note": ("原备注", "text", ("备注", "说明")),
    "currency": ("币种", "text", ("币种", "货币")),
    "linked_ticker": ("明确关联证券（IPO等）", "identifier", ("关联证券", "关联代码")),
}
KINDS = {"证券买入":"buy", "买入":"buy", "买":"buy", "buy":"buy", "证券卖出":"sell", "卖出":"sell", "卖":"sell", "sell":"sell",
         "证券转银行":"cash_withdrawal", "银行转证券":"cash_deposit", "新股入账":"stock_credit", "配售缴款":"ipo_payment",
         "利息归本":"interest", "股息红利差异扣税":"dividend_tax", "红利入账":"dividend"}
KINDS.update({value:value for value in tuple(KINDS.values()) + ("corporate_action", "other")})
SENSITIVE = re.compile(r"账号|账户|股东号|客户号|身份证|银行|资金帐号|股东代码")
COMPONENTS = ("commission", "regulatory_fee", "stamp_tax", "transfer_fee")


class ImportFileError(TradeJournalError):
    pass


def _text(value):
    return "" if value is None else str(value).strip()


def _display(value, label=""):
    if value is None:
        return None
    if SENSITIVE.search(label) or re.search(r"账号|帐号|账户|股东号|客户号|身份证|客户姓名|开户姓名",_text(value)):
        return "[账户字段已脱敏]"
    return re.sub(r"\d{9,}", "[长标识已脱敏]", _text(value))[:240]


def _decode(filename, content_base64):
    if not isinstance(filename, str) or not filename or not isinstance(content_base64, str):
        raise ImportFileError("文件名与原件base64不能为空")
    if len(content_base64) > ((LIMITS["max_file_bytes"]+2)//3)*4+4:
        raise ImportFileError("原件超过8MiB限制")
    try:
        raw = base64.b64decode(content_base64, validate=True)
    except (ValueError, TypeError) as error:
        raise ImportFileError("原件base64无效") from error
    if not raw or len(raw) > LIMITS["max_file_bytes"]:
        raise ImportFileError("原件为空或超过8MiB限制")
    return raw, hashlib.sha256(raw).hexdigest()


def _reserve_row(width, counters):
    counters[0] += 1
    counters[1] += width
    if counters[0] > LIMITS["max_rows"] or width > LIMITS["max_columns"] or counters[1] > LIMITS["max_cells"]:
        raise ImportFileError("原件行列/单元格总数超过限制")


def _bounded_row(cells):
    if any(len(_text(cell["value"])) > LIMITS["max_cell_characters"] for cell in cells):
        raise ImportFileError("原件单元格文本过长")
    return cells


def _validate_worksheet(content,counters):
    """Validate physical coordinates before openpyxl can synthesize blanks."""
    first,row_width=True,0
    for event,node in ElementTree.iterparse(BytesIO(content),events=("start","end")):
        tag=node.tag.rsplit("}",1)[-1]
        if first:
            first=False
            if tag!="worksheet":return
        if event=="start" and tag=="row":
            counters[0]+=1;row_width=0
            if counters[0]>LIMITS["max_rows"]:raise ImportFileError("原始工作表总行数超过限制")
        if event=="start" and tag=="c":
            counters[1]+=1;row_width+=1
            if counters[1]>LIMITS["max_cells"] or row_width>LIMITS["max_columns"]:
                raise ImportFileError("原始工作表总单元格/列数超过限制")
        if event=="start" and tag=="row" and node.get("r") is not None:
            if not node.get("r").isdigit() or not 1<=int(node.get("r"))<=LIMITS["max_rows"]:
                raise ImportFileError("原始工作表行坐标超过限制")
        if event=="start" and tag=="c" and node.get("r"):
            match=re.fullmatch(r"([A-Z]+)([1-9][0-9]*)",node.get("r"))
            if not match:raise ImportFileError("原始工作表单元格坐标无效")
            column=0
            for letter in match[1]:
                column=column*26+ord(letter)-64
                if column>LIMITS["max_columns"]:raise ImportFileError("原始工作表列坐标超过限制")
            if int(match[2])>LIMITS["max_rows"]:raise ImportFileError("原始工作表行坐标超过限制")
        if event=="end" and tag in ("c","row"):node.clear()


def _xlsx(raw):
    try:
        archive = zipfile.ZipFile(BytesIO(raw))
        entries = archive.infolist()
        if len(entries) > LIMITS["max_zip_members"] or sum(e.file_size for e in entries) > LIMITS["max_uncompressed_bytes"]:
            raise ImportFileError("工作簿解压规模超过限制")
        names = [e.filename for e in entries]
        if len(names) != len(set(names)) or any(e.flag_bits & 1 or ".." in PurePath(e.filename).parts or e.filename.startswith("/") or "\\" in e.filename for e in entries):
            raise ImportFileError("工作簿含重复、加密或不安全压缩条目")
        if any("vbaproject" in n.lower() or "externallinks/" in n.lower() for n in names):
            raise ImportFileError("不支持宏或外部工作簿链接")
        if "[Content_Types].xml" not in names or "xl/workbook.xml" not in names:
            raise ImportFileError("原件不是有效XLSX工作簿")
        physical_counts=[0,0]
        for entry in entries:
            if entry.filename.lower().endswith((".xml", ".rels")):
                content = archive.read(entry)
                if b"<!DOCTYPE" in content.upper() or b"<!ENTITY" in content.upper():
                    raise ImportFileError("工作簿XML不允许DTD或实体")
                if entry.filename.lower().endswith(".rels") and re.search(rb'TargetMode\s*=\s*[\x22\x27]External', content, re.I):
                    raise ImportFileError("工作簿包含外部链接，当前适配器明确拒绝")
                if entry.filename.lower().endswith(".xml"):
                    _validate_worksheet(content,physical_counts)
        archive.close()
        import openpyxl
        book = openpyxl.load_workbook(BytesIO(raw), read_only=True, data_only=False, keep_links=False)
        try:
            if len(book.worksheets) > LIMITS["max_sheets"]:
                raise ImportFileError("工作表数量超过限制")
            sheets, counters = {}, [0,0]
            for sheet in book.worksheets:
                # OOXML dimensions are advisory and can both inflate blank
                # rows and truncate actual source rows. Physical coordinates
                # were checked above; stream every actual row instead.
                sheet.reset_dimensions()
                rows = []
                for cells in sheet.iter_rows():
                    if len(rows) >= LIMITS["max_rows"] or len(cells) > LIMITS["max_columns"]:
                        raise ImportFileError("工作表行列数超过限制")
                    _reserve_row(len(cells),counters)
                    rows.append(_bounded_row([{"value":c.value, "formula":c.data_type == "f"} for c in cells]))
                sheets[sheet.title] = rows
            return sheets
        finally:
            book.close()
    except ImportFileError:
        raise
    except Exception as error:
        raise ImportFileError("XLSX原件无法安全读取；请核对格式或重新导出原始表") from error


def _xls(raw):
    try:
        import xlrd
        from xlrd.compdoc import CompDoc
    except ImportError as error:
        raise ImportFileError("当前环境没有XLS读取依赖，请导出XLSX或CSV") from error
    try:
        doc = CompDoc(raw, logfile=StringIO())
        if any("vba" in item.name.casefold() or item.name.casefold() == "macros" for item in doc.dirlist):
            raise ImportFileError("XLS含宏存储，当前适配器拒绝")
        stream = doc.get_named_stream("Workbook") or doc.get_named_stream("Book")
        if not stream:
            raise ImportFileError("XLS缺Workbook流")
        offset, dimension_cells = 0, 0
        while offset + 4 <= len(stream):
            code, size = struct.unpack_from("<HH", stream, offset)
            if code in (0x0006, 0x0206, 0x0406, 0x0017):
                raise ImportFileError("XLS含公式或外部引用；即使有缓存值也不作为原始事实读取")
            if code==0x0200 and size in (10,14):
                last_row=struct.unpack_from("<I" if size==14 else "<H",stream,offset+8 if size==14 else offset+6)[0]
                last_col=struct.unpack_from("<H",stream,offset+14 if size==14 else offset+10)[0]
                dimension_cells+=last_row*last_col
                if last_row>LIMITS["max_rows"] or last_col>LIMITS["max_columns"] or dimension_cells>LIMITS["max_cells"]:
                    raise ImportFileError("XLS声明的行列/稀疏补齐规模超过限制")
            offset += size + 4
        book = xlrd.open_workbook(file_contents=raw, on_demand=True, logfile=StringIO())
        try:
            if book.nsheets > LIMITS["max_sheets"]:
                raise ImportFileError("工作表数量超过限制")
            sheets, counters = {}, [0,0]
            for sheet_index in range(book.nsheets):
                sheet=book.sheet_by_index(sheet_index)
                if sheet.nrows > LIMITS["max_rows"] or sheet.ncols > LIMITS["max_columns"]:
                    raise ImportFileError("工作表行列数超过限制")
                rows = []
                for index in range(sheet.nrows):
                    _reserve_row(sheet.ncols,counters)
                    cells = []
                    for cell in sheet.row(index):
                        value = xlrd.xldate_as_datetime(cell.value, book.datemode) if cell.ctype == xlrd.XL_CELL_DATE else cell.value
                        cells.append({"value":value, "formula":False})
                    rows.append(_bounded_row(cells))
                sheets[sheet.name] = rows
                book.unload_sheet(sheet_index)
            return sheets
        finally:
            book.release_resources()
    except ImportFileError:
        raise
    except Exception as error:
        raise ImportFileError("XLS无法安全读取，请导出未含公式/宏的XLSX或CSV") from error


def _read(filename, raw, encoding):
    suffix = PurePath(filename).suffix.lower()
    if suffix == ".xlsx":
        return _xlsx(raw), "xlsx", None
    if suffix == ".xls":
        return _xls(raw), "xls", None
    if suffix not in (".csv", ".tsv"):
        raise ImportFileError("仅支持实际XLSX、XLS或CSV/TSV文件；不接受改扩展名的HTML")
    if b"\x00" in raw or raw.lstrip().startswith((b"<", b"PK")):
        raise ImportFileError("CSV原件包含二进制/HTML内容，不作表格猜测")
    content, selected = None, None
    for candidate in ([encoding] if encoding else ["utf-8-sig", "gb18030"]):
        if candidate not in ("utf-8", "utf-8-sig", "gb18030"):
            raise ImportFileError("CSV编码只支持UTF-8或GB18030")
        try:
            content, selected = raw.decode(candidate), candidate
            break
        except UnicodeDecodeError:
            pass
    if content is None:
        raise ImportFileError("CSV无法按所选编码读取")
    try:
        dialect = csv.Sniffer().sniff(content[:8192], delimiters=",\t;")
    except csv.Error:
        dialect = csv.excel_tab if suffix == ".tsv" else csv.excel
    rows, counters = [], [0,0]
    try:
        for row in csv.reader(StringIO(content), dialect):
            if len(rows) >= LIMITS["max_rows"] or len(row) > LIMITS["max_columns"]:
                raise ImportFileError("CSV行列数超过限制")
            _reserve_row(len(row),counters)
            rows.append(_bounded_row([{"value":value, "formula":value.lstrip().startswith("=")} for value in row]))
    except csv.Error as error:
        raise ImportFileError("CSV记录格式无效") from error
    return {"CSV":rows}, "csv", selected


def _load(filename, content_base64, encoding):
    raw, sha = _decode(filename, content_base64)
    sheets, kind, selected = _read(filename, raw, encoding)
    if sum(len(row) for rows in sheets.values() for row in rows) > LIMITS["max_cells"]:
        raise ImportFileError("工作簿总单元格数超过限制")
    if sum(len(rows) for rows in sheets.values()) > LIMITS["max_rows"]:
        raise ImportFileError("原件总行数超过限制")
    if any(len(_text(cell["value"])) > LIMITS["max_cell_characters"] for rows in sheets.values() for row in rows for cell in row):
        raise ImportFileError("原件单元格文本过长")
    return raw, sha, sheets, kind, selected


def inspect_file(filename, content_base64, *, encoding=None, sheet=None, row_start=1, row_limit=100):
    raw, sha, sheets, kind, selected = _load(filename, content_base64, encoding)
    if type(row_start) is not int or row_start < 1 or type(row_limit) is not int or not 1 <= row_limit <= 200:
        raise ImportFileError("原行分页row_start须为正整数，row_limit须为1至200整数")
    if sheet is not None and (not isinstance(sheet,str) or sheet not in sheets):
        raise ImportFileError("指定工作表不存在")
    output, suggestions, ignored = [], [], []
    for name, rows in sheets.items():
        headers = []
        for number, cells in enumerate(rows[:40], 1):
            columns, recognized = [], set()
            for index, cell in enumerate(cells, 1):
                value = _text(cell["value"])
                matches = [key for key, (_,_,aliases) in LABELS.items() if value in aliases]
                recognized.update(matches)
                columns.append({"index":index, "label":_display(value), "field_candidates":matches, "sensitive":bool(SENSITIVE.search(value))})
            if {"code", "kind"} <= recognized and len(recognized) >= 4:
                headers.append({"row":number, "columns":columns})
        header = headers[0] if headers else None
        labels = [_text(c["value"]) for c in rows[header["row"]-1]] if header else []
        sample_start = header["row"] if header else 0
        samples = [{"row":i+1, "cells":[_display(c["value"], labels[j] if j < len(labels) else "") for j,c in enumerate(row)]}
                   for i,row in enumerate(rows) if sample_start <= i < sample_start+5]
        has_formulas = any(c["formula"] for row in rows[sample_start:] for c in row)
        previews = [{"row":i+1,"cells":[_display(c["value"], labels[j] if j < len(labels) else "") for j,c in enumerate(row)]}
                    for i,row in enumerate(rows) if row_start-1 <= i < row_start-1+row_limit] if sheet in (None,name) else []
        output.append({"name":name, "row_count":len(rows), "column_count":max((len(r) for r in rows), default=0),
                       "candidate_headers":headers, "sample_rows":samples, "contains_formulas":has_formulas,
                       "row_previews":previews,"row_start":row_start,"row_limit":row_limit})
        if header and not has_formulas:
            columns = {}
            for col in header["columns"]:
                if len(col["field_candidates"]) == 1:
                    field = col["field_candidates"][0]
                    if field not in columns:
                        columns[field] = col["index"]
            role = "corroborating_executions" if "broker_execution_id" in columns and "net_cash" not in columns else "events"
            days = set(re.findall(r"20\d{2}-\d{2}-\d{2}", " ".join(_text(c["value"]) for row in rows[:header["row"]-1] for c in row)))
            suggestions.append({"sheet":name, "header_row":header["row"], "columns":columns, "role":role,
                                "date_default":next(iter(days)) if len(days) == 1 and "date" not in columns else None,
                                "time_role":"statement_event_time" if "statement_event_time" in columns else "execution_time" if "execution_time" in columns else "date_only",
                                "cash_sign":"signed", "currency_default":"CNY"})
        else:
            ignored.append({"sheet":name, "reason":""})
    # A standalone execution export is itself the primary source.
    if suggestions and all(t["role"] == "corroborating_executions" for t in suggestions):
        suggestions[0]["role"] = "events"
    return {"file_id":"file-"+sha, "sha256":sha, "parser_version":VERSION, "format":kind,
            "filename":_display(PurePath(filename).name), "bytes":len(raw), "encoding":selected, "sheets":output,
            "fields":[{"id":key,"label":label,"type":typ} for key,(label,typ,_) in LABELS.items()],
            "limits":deepcopy(LIMITS), "warnings":["建议映射须逐表确认；原件账户字段仅在界面脱敏，private base64和SHA保持原字节。",
                "公式（含有缓存值）不作为事实；汇总/复盘表应明确忽略，原始表仍须逐行核对。"],
            "suggested_mapping":{"tables":suggestions, "ignored_sheets":ignored, "ignored_rows":[], "corroboration_pairs":[]}}


def _number(value, label, *, integer=False):
    if value is None or _text(value) in ("", "--", "—", "-"):
        return None
    if isinstance(value, bool):
        raise ImportFileError(label+"不是有效数值")
    text = _text(value).replace("，", ",")
    if text.startswith("(") and text.endswith(")"):
        text = "-"+text[1:-1]
    if "," in text:
        if not re.fullmatch(r"[+-]?\d{1,3}(,\d{3})+(\.\d+)?",text):
            raise ImportFileError(label+"的千分位分组无效，不猜测小数分隔符")
        text = text.replace(",", "")
    try:
        number = Decimal(text)
    except InvalidOperation as error:
        raise ImportFileError(label+"不是有效数值") from error
    if not number.is_finite() or integer and number != number.to_integral_value():
        raise ImportFileError(label+"必须为有限"+("整数" if integer else "数值"))
    if not math.isfinite(float(number)):
        raise ImportFileError(label+"超出有限数值范围")
    return int(number) if integer else float(number)


def _date(value):
    if isinstance(value, (datetime,date)):
        return value.date().isoformat() if isinstance(value,datetime) else value.isoformat()
    text = _text(value).replace("/", "-").replace("年", "-").replace("月", "-").replace("日", "")
    if re.fullmatch(r"\d{8}", text):
        text = text[:4]+"-"+text[4:6]+"-"+text[6:]
    try:
        return date.fromisoformat(text[:10]).isoformat()
    except ValueError as error:
        raise ImportFileError("日期缺失或无效；不从文件名或今天猜测") from error


def _clock(value):
    if value is None or _text(value) == "":
        return ""
    if isinstance(value, datetime):
        return value.strftime("%H:%M:%S")
    if isinstance(value, time):
        return value.strftime("%H:%M:%S")
    text = _text(value)
    if re.fullmatch(r"\d{6}", text):
        text = ":".join((text[:2], text[2:4], text[4:]))
    try:
        return time.fromisoformat(text[-8:] if " " in text or "T" in text else text).strftime("%H:%M:%S")
    except ValueError as error:
        raise ImportFileError("时间须为可核对的HH:MM:SS，不把数值擅当Excel时间") from error


def _code(value):
    if isinstance(value, (int,float)) and not isinstance(value,bool):
        value = _number(value, "证券代码", integer=True)
        value = str(value)
    text = _text(value).upper()
    if re.fullmatch(r"\d{1,6}", text):
        return text.zfill(6)
    if text in ("", "-", "--", "—", "CASH"):
        return "CASH"
    if re.fullmatch(r"\d{6}\.(SH|SZ|BJ)", text):
        return text
    raise ImportFileError("证券代码无效，不猜测或丢失前导零")


def _fact(values, table, source_id, sheet, number):
    kind = {**KINDS, **(table.get("kind_map") or {})}.get(_text(values.get("kind")))
    if kind not in set(KINDS.values()):
        raise ImportFileError("业务类别未识别，须显式映射或注明忽略理由")
    code = _code(values.get("code"))
    currency = _text(values.get("currency") or table.get("currency_default"))
    if currency not in ("人民币", "人民币元", "CNY", "RMB"):
        raise ImportFileError("币种未明确为人民币，不能套用人民币账本")
    day = _date(values.get("date") or table.get("date_default"))
    raw_id = values.get("broker_execution_id")
    if raw_id is not None and _text(raw_id) and not isinstance(raw_id, str):
        raise ImportFileError("成交编号不是文本，无法确认前导零/Excel精度；请按文本重新导出")
    true_time = _clock(values.get("execution_time"))
    statement_time = _clock(values.get("statement_event_time"))
    generic = _clock(values.get("time"))
    role = table.get("time_role", "date_only")
    if role not in ("execution_time", "statement_event_time", "date_only"):
        raise ImportFileError("须明确时间语义")
    if generic:
        if role == "execution_time": true_time = true_time or generic
        elif role == "statement_event_time": statement_time = statement_time or generic
        else: raise ImportFileError("已映射时间列但声明date_only，请明确发生/成交语义")
    fields = {key:_number(values.get(key), LABELS[key][0], integer=key in ("quantity", "quantity_after"))
              for key in ("quantity", "quantity_after", "price", "amount", "net_cash", "fees", *COMPONENTS)}
    if any(fields[key] is not None and fields[key] < 0 for key in ("fees",*COMPONENTS)):
        raise ImportFileError("费用及各费用分项须非负，缺失不可补0")
    if fields["fees"] is None and all(key in table["columns"] and fields[key] is not None for key in COMPONENTS):
        fields["fees"] = float(sum(Decimal(str(fields[key])) for key in COMPONENTS))
    cash = fields["net_cash"]
    cash_sign = table.get("cash_sign", "signed")
    negative = kind in ("buy", "cash_withdrawal", "ipo_payment", "dividend_tax")
    if cash_sign == "directional_magnitude" and cash is not None:
        if cash < 0: raise ImportFileError("所选金额为无符号收付额，但原值为负；不能静默取绝对值")
        cash = -cash if negative else cash
    elif cash_sign != "signed":
        raise ImportFileError("须明确净收付金额符号语义")
    if cash is not None and kind not in ("corporate_action","other") and (negative and cash > 0 or not negative and cash < 0):
        raise ImportFileError("净收付符号与业务类别矛盾，须核对金额映射")
    result = {"code":code, "name":_text(values.get("name")) or code, "date":day,
              "time":true_time or statement_time, "statement_event_time":statement_time or None,
              "time_precision":"execution_time" if true_time else "statement_event_time" if statement_time else "date_only",
              "quantity":fields["quantity"], "quantity_after":fields["quantity_after"], "net_cash":cash,
              "source_ref":{"source_id":source_id,"sheet":sheet,"row":number}, "note":_text(values.get("note")) or None}
    if kind in ("buy","sell"):
        if code == "CASH" or fields["quantity"] is None or fields["quantity"] <= 0 or fields["price"] is None or fields["price"] <= 0:
            raise ImportFileError("买卖必须有明确证券、正股数和成交价格")
        if fields["amount"] is None:
            raise ImportFileError("成交金额缺失，不在文件适配层补算源事实")
        if fields["amount"] < 0 or fields["fees"] is not None and fields["fees"] < 0:
            raise ImportFileError("成交金额与费用须非负；净收付另有符号")
        result.update(side=kind, price=fields["price"], amount=fields["amount"], fees=fields["fees"], broker_execution_id=_text(raw_id) or None)
    else:
        if fields["quantity"] is None:
            raise ImportFileError("财务事件数量缺失，不能假定为0；源值为0可以保留")
        result.update(kind=kind, settlement_date=_date(values.get("settlement_date") or day), linked_ticker=_text(values.get("linked_ticker")) or None)
    return result


def _signature(row):
    return tuple(row.get(k) for k in ("date","code","side","quantity","price","amount"))


def parse_file(filename, content_base64, *, mapping, account_alias="primary", encoding=None):
    raw, sha, sheets, kind, selected = _load(filename, content_base64, encoding)
    if not isinstance(mapping, dict) or not isinstance(mapping.get("tables"), list) or not mapping["tables"]:
        raise ImportFileError("须提交经确认的工作表/表头/列映射")
    try:
        if len(BSON.encode(mapping)) > LIMITS["max_mapping_bytes"]:
            raise ImportFileError("映射说明超过2MiB限制，请拆分原始导出文件")
    except ImportFileError:
        raise
    except (OverflowError,ValueError,TypeError) as error:
        raise ImportFileError("映射须为可归档的有限JSON字段") from error
    for key in ("ignored_sheets","ignored_rows","corroboration_pairs"):
        if key in mapping and not isinstance(mapping[key],list):
            raise ImportFileError(key+"须为数组")
    for key in ("ignored_sheets","ignored_rows"):
        seen = set()
        for item in mapping.get(key,[]):
            if not isinstance(item,dict) or not isinstance(item.get("sheet"),str) or not isinstance(item.get("reason"),str):
                raise ImportFileError("忽略项须含工作表名称与文本理由")
            if key == "ignored_rows" and (type(item.get("row")) is not int or item["row"] < 1):
                raise ImportFileError("忽略行号须为从1开始的整数")
            identity = (item["sheet"],item["row"]) if key == "ignored_rows" else item["sheet"]
            if identity in seen: raise ImportFileError("忽略项不能重复")
            seen.add(identity)
    if not isinstance(account_alias, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_.:-]{0,63}", account_alias) or re.search(r"\d{9,}", account_alias):
        raise ImportFileError("账户只使用内部alias，不能传入原券商或银行账号")
    source_id = "broker-file-"+sha
    issues, fills, financial, extras, joins = [], [], [], [], []
    table_labels, used_ignores = {}, set()
    ignored_sheets = {x.get("sheet"):x.get("reason") for x in mapping.get("ignored_sheets",[]) if isinstance(x,dict)}
    ignored_rows = {(x.get("sheet"),x.get("row")):x.get("reason") for x in mapping.get("ignored_rows",[]) if isinstance(x,dict)}
    def issue(code,message,sheet=None,row=None):
        record = {"code":code,"message":message,**({"sheet":sheet} if sheet is not None else {}),**({"row":row} if row is not None else {})}
        if sheet in sheets and type(row) is int and 1 <= row <= len(sheets[sheet]):
            labels = table_labels.get(sheet,[])
            record.update(columns=[_display(label) for label in labels],cells=[_display(c["value"],labels[j] if j < len(labels) else "") for j,c in enumerate(sheets[sheet][row-1])])
        issues.append(record)
    selected_sheets = set()
    total_rows = ignored_count = 0
    for table in mapping["tables"]:
        if not isinstance(table,dict): raise ImportFileError("每个工作表映射须为对象")
        sheet, header, columns = table.get("sheet"), table.get("header_row"), table.get("columns")
        if not isinstance(sheet,str) or sheet not in sheets or sheet in selected_sheets or sheet in ignored_sheets:
            raise ImportFileError("工作表不存在、重复选择或同时声明忽略")
        selected_sheets.add(sheet)
        if type(header) is not int or not 1 <= header <= len(sheets[sheet]) or not isinstance(columns,dict):
            raise ImportFileError("表头行或列映射无效")
        if not {"code","kind"} <= columns.keys() or any(key not in LABELS or type(col) is not int or not 1 <= col <= LIMITS["max_columns"] for key,col in columns.items()):
            raise ImportFileError("映射须有code/kind，列序号从1开始且只能使用已定义字段")
        if "kind_map" in table and (not isinstance(table["kind_map"],dict) or any(not isinstance(k,str) or not isinstance(v,str) or v not in set(KINDS.values()) for k,v in table["kind_map"].items())):
            raise ImportFileError("业务映射须为原业务文本到已定义业务类别的对象")
        table_labels[sheet] = [_text(c["value"]) for c in sheets[sheet][header-1]]
        role = table.get("role", "events")
        if role not in ("events","corroborating_executions"): raise ImportFileError("未知工作表角色")
        for number,cells in enumerate(sheets[sheet],1):
            if number <= header or not any(_text(c["value"]) for c in cells): continue
            total_rows += 1
            if (sheet,number) in ignored_rows:
                used_ignores.add((sheet,number))
                if not _text(ignored_rows[(sheet,number)]): issue("ignore_reason_missing","忽略原行必须填写理由",sheet,number)
                else: ignored_count += 1
                continue
            if any(cells[col-1]["formula"] for col in columns.values() if col <= len(cells)):
                issue("formula_not_supported","映射事实含公式，即使存在缓存值也不读取",sheet,number);continue
            values = {key:cells[col-1]["value"] if col <= len(cells) else None for key,col in columns.items()}
            try:
                fact = _fact(values,table,source_id,sheet,number)
                if role == "corroborating_executions":
                    if "side" not in fact or fact["time_precision"] != "execution_time": raise ImportFileError("旁证表须为明确实际成交时间的买卖记录")
                    extras.append(fact)
                elif "side" in fact: fills.append(fact)
                else: financial.append(fact)
            except (ImportFileError,ValueError,TypeError) as error:
                issue("unresolved_row",str(error),sheet,number)
    for sheet,rows in sheets.items():
        if sheet not in selected_sheets and any(_text(c["value"]) for row in rows for c in row) and not _text(ignored_sheets.get(sheet)):
            issue("unmapped_sheet","非空工作表未选择或未提供忽略理由",sheet)
    for sheet in ignored_sheets:
        if sheet not in sheets: issue("unknown_ignored_sheet","忽略的工作表不存在",sheet)
    for sheet,number in ignored_rows:
        if (sheet,number) not in used_ignores:
            issue("unused_ignored_row","忽略行不存在、为空、属于表头前或不在所选原始表，须核对原行定位",sheet,number)
    if not fills and not financial: issue("empty_events","没有可作为主事实的成交/财务事件")
    pairs = {}
    try:
        for pair in mapping.get("corroboration_pairs",[]):
            p,e=pair["primary"],pair["execution"]
            if not all(isinstance(x,dict) and isinstance(x.get("sheet"),str) and type(x.get("row")) is int and x["row"] > 0 for x in (p,e)):
                raise ValueError("原行定位无效")
            key=(p["sheet"],p["row"])
            if key in pairs: raise ValueError("同一原始行不能配对多次")
            pairs[key]=(e["sheet"],e["row"])
    except (KeyError,TypeError,ValueError) as error:
        raise ImportFileError("旁证行对须为primary/execution各含sheet/row，且不能重复") from error
    fill_locations={(f["source_ref"]["sheet"],f["source_ref"]["row"]) for f in fills}
    extra_locations={(f["source_ref"]["sheet"],f["source_ref"]["row"]) for f in extras}
    for primary,execution in pairs.items():
        if primary not in fill_locations or execution not in extra_locations:
            issue("invalid_corroboration_pair","明确行对须定位到已解析主成交与成交旁证行",primary[0],primary[1])
    used = set()
    for fact in fills:
        key=(fact["source_ref"]["sheet"],fact["source_ref"]["row"])
        candidates=[i for i,other in enumerate(extras) if i not in used and _signature(fact)==_signature(other)
                    and (not fact.get("broker_execution_id") or fact["broker_execution_id"]==other.get("broker_execution_id"))]
        if key in pairs:
            candidates=[i for i in candidates if (extras[i]["source_ref"]["sheet"],extras[i]["source_ref"]["row"])==pairs[key]]
        if extras and len(candidates)!=1:
            issue("ambiguous_corroboration","双表未唯一对应，须提供明确原行对；不按时间/余额排序猜测",key[0],key[1]);continue
        if not candidates: continue
        i=candidates[0]; other=extras[i];used.add(i)
        if fact.get("fees") is not None and other.get("fees") is not None and Decimal(str(fact["fees"])) != Decimal(str(other["fees"])):
            issue("corroboration_conflict","双表费用矛盾，不选择其中一方覆盖",key[0],key[1]);continue
        if fact["time_precision"] == "execution_time" and fact["time"] != other["time"]:
            issue("corroboration_conflict","双表均声明真实成交时间但时间矛盾，不能覆盖",key[0],key[1]);continue
        statement_time = fact.get("statement_event_time") or (fact.get("time") if fact["time_precision"] != "execution_time" else None)
        fact.update(time=other["time"],time_precision="execution_time",broker_execution_id=other.get("broker_execution_id"),statement_event_time=statement_time)
        if fact["fees"] is None: fact["fees"]=other.get("fees")
        joins.append({"primary":deepcopy(fact["source_ref"]),"execution":deepcopy(other["source_ref"])})
    for i,row in enumerate(extras):
        if i not in used: issue("unmatched_corroboration","成交旁证未对应到主事实，不能静默漏入或重复新增",row["source_ref"]["sheet"],row["source_ref"]["row"])
    broker_ids = set()
    for fact in fills:
        key=(fact["date"],fact["code"],fact.get("broker_execution_id"))
        if fact.get("broker_execution_id") and key in broker_ids:
            issue("duplicate_execution_identity","同一券商成交编号在主事实重复，须核对工作表角色/重复来源",fact["source_ref"]["sheet"],fact["source_ref"]["row"])
        broker_ids.add(key)
    audit={"parser_version":VERSION,"total_data_rows":total_rows,"parsed_fill_rows":len(fills),"financial_event_rows":len(financial),
           "corroborated_rows":len(joins),"ignored_rows":ignored_count,"ignored_sheets":deepcopy(ignored_sheets),"unresolved_rows":len(issues),
           "corroboration_pairs":joins,"time_note":"发生/成交时间分别保留；原余额不自动等于成交时点快照", "correction_policy":"原件与事实追加归档；更正须独立审核，不破坏性替换既有事实"}
    source={"source_id":source_id,"filename":_display(PurePath(filename).name),"sha256":sha,"content_encoding":"base64","content":content_base64,
            "source_type":"broker_file_archive","evidence_class":"historical_reconstruction",
            "metadata":{"parser_version":VERSION,"format":kind,"encoding":selected,"mapping":deepcopy(mapping),"audit":audit}}
    if len(BSON.encode(source)) > LIMITS["max_source_bson_bytes"]:
        raise ImportFileError("原件base64与完整映射/原行审计合计超过14MiB归档预算，请拆分原始导出；不裁断原件或审计")
    payload={"account_alias":account_alias,"sources":[source],"fills":fills,"financial_events":financial} if not issues else None
    return {"can_preview":not issues,"payload":payload,"issues":issues,"audit":audit,"mapping_digest":digest(mapping),"sha256":sha,"parser_version":VERSION}


__all__=["VERSION","LIMITS","ImportFileError","inspect_file","parse_file"]
