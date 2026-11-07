"""File adapters preserve source facts; journal preview owns accounting/import."""
import base64
from copy import deepcopy
import hashlib
from io import BytesIO
from pathlib import Path
import zipfile
from xml.sax.saxutils import escape, quoteattr

import pytest

from shilun.trade_journal import import_files as adapter
from shilun.trade_journal.service import TradeJournalError, TradeJournalService
from test_trade_journal import FakeStore


HEADER = ["发生日期","发生时间","证券代码","证券名称","买卖类别","成交数量","成交价格","成交金额","发生金额","佣金","交易规费","印花税","过户费","股票余额","币种"]
BUY = ["2026-10-19","13:05:00","000636","风华高科","证券买入",100,10,1000,-1001,1,0,0,0,100,"人民币"]
EXEC_HEADER = ["成交日期","成交时间","证券代码","证券名称","委托方向","成交数量","成交价格","成交金额","成交编号","币种"]
EXEC = ["2026-10-19","10:00:01","000636","风华高科","买入",100,10,1000,"000012345678901234","人民币"]


def encoded(raw):
    return base64.b64encode(raw).decode()


def workbook(sheets, *, extra=None):
    """Tiny OOXML source fixture, including cached formulas without evaluating."""
    members = {"[Content_Types].xml":'<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'+''.join(f'<Override PartName="/xl/worksheets/sheet{i}.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>' for i in range(1,len(sheets)+1))+'</Types>',
        "_rels/.rels":'<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>',
        "xl/workbook.xml":'<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets>'+''.join(f'<sheet name={quoteattr(name)} sheetId="{i}" r:id="rId{i}"/>' for i,name in enumerate(sheets,1))+'</sheets></workbook>',
        "xl/_rels/workbook.xml.rels":'<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'+''.join(f'<Relationship Id="rId{i}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet{i}.xml"/>' for i in range(1,len(sheets)+1))+'</Relationships>'}
    for index,rows in enumerate(sheets.values(),1):
        body=[]
        for number,row in enumerate(rows,1):
            cells=[]
            for value in row:
                if isinstance(value,tuple):
                    cells.append('<c><f>'+escape(value[0])+'</f><v>'+str(value[1])+'</v></c>')
                elif isinstance(value,(float,int)):
                    cells.append('<c t="n"><v>'+str(value)+'</v></c>')
                else:
                    cells.append('<c t="inlineStr"><is><t>'+escape('' if value is None else str(value))+'</t></is></c>')
            body.append(f'<row r="{number}">'+''.join(cells)+'</row>')
        members[f'xl/worksheets/sheet{index}.xml']='<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>'+''.join(body)+'</sheetData></worksheet>'
    members.update(extra or {})
    out=BytesIO()
    with zipfile.ZipFile(out,'w',zipfile.ZIP_DEFLATED) as archive:
        for key,value in members.items():archive.writestr(key,value)
    return out.getvalue()


def inspect_parse(sheets, *, change=None):
    raw=workbook(sheets)
    mapping=adapter.inspect_file('东财.xlsx',encoded(raw))['suggested_mapping']
    if change: change(mapping)
    return raw,mapping,adapter.parse_file('东财.xlsx',encoded(raw),mapping=mapping)


def test_raw_hash_original_row_time_and_leading_zero_reach_existing_preview():
    raw,mapping,result=inspect_parse({'原表':[['导出说明'],HEADER,BUY]})
    assert result['can_preview'] and not result['issues']
    source=result['payload']['sources'][0]
    assert base64.b64decode(source['content'])==raw
    assert source['sha256']==hashlib.sha256(raw).hexdigest()
    assert source['metadata']['mapping']==mapping
    row=result['payload']['fills'][0]
    assert row['code']=='000636' and row['source_ref']['row']==3
    assert row['time_precision']=='statement_event_time' and row['time']=='13:05:00'
    assert row['fees']==1 and row['net_cash']==-1001
    store=FakeStore();preview=TradeJournalService(store).preview_import(result['payload'])
    assert preview['can_import'] and preview['counts']['new_fills']==1 and not store.writes


def test_dual_raw_sheets_are_one_fact_with_both_time_semantics_and_private_id():
    raw,mapping,result=inspect_parse({'交割单': [HEADER,BUY], '实际成交':[EXEC_HEADER,EXEC]})
    assert result['can_preview'] and result['audit']['corroborated_rows']==1
    row=result['payload']['fills'][0]
    assert row['time']=='10:00:01' and row['statement_event_time']=='13:05:00'
    assert row['time_precision']=='execution_time'
    assert row['broker_execution_id']==EXEC[8]
    inspection=adapter.inspect_file('东财.xlsx',encoded(raw),sheet='实际成交',row_start=2,row_limit=1)
    assert inspection['sheets'][0]['row_previews']==[]
    assert inspection['sheets'][1]['row_previews'][0]['cells'][8]=='[长标识已脱敏]'
    assert len(result['payload']['fills'])==1 and result['audit']['corroboration_pairs'][0]['execution']['sheet']=='实际成交'


def test_repeated_same_price_quantity_requires_explicit_pairs_not_temporal_sort():
    second=deepcopy(EXEC);second[1]='09:30:01';second[8]='00000002'
    raw,mapping,result=inspect_parse({'交割单':[HEADER,BUY,BUY], '实际成交':[EXEC_HEADER,EXEC,second]})
    assert not result['can_preview'] and result['payload'] is None
    assert 'ambiguous_corroboration' in {r['code'] for r in result['issues']}
    mapping['corroboration_pairs']=[{'primary':{'sheet':'交割单','row':2},'execution':{'sheet':'实际成交','row':3}}, {'primary':{'sheet':'交割单','row':3},'execution':{'sheet':'实际成交','row':2}}]
    fixed=adapter.parse_file('东财.xlsx',encoded(raw),mapping=mapping)
    assert fixed['can_preview'] and [r['time'] for r in fixed['payload']['fills']]==['09:30:01','10:00:01']
    mapping['corroboration_pairs'][0]['execution']['row']=99
    assert not adapter.parse_file('东财.xlsx',encoded(raw),mapping=mapping)['can_preview']


def test_unknown_rows_and_excluded_worksheets_require_explicit_reasons():
    unknown=deepcopy(BUY);unknown[4]='未识别业务'
    raw,mapping,result=inspect_parse({'原表':[HEADER,BUY,unknown],'复盘':['摘要']})
    assert result['payload'] is None
    problem=next(i for i in result['issues'] if i['code']=='unresolved_row')
    assert problem['row']==3 and problem['cells'][4]=='未识别业务' and problem['columns'][0]=='发生日期'
    mapping['ignored_sheets']=[{'sheet':'复盘','reason':'手工复盘摘要不作为券商事实'}]
    mapping['ignored_rows']=[{'sheet':'原表','row':3,'reason':'已核实为备注，留原件不入事实'}]
    fixed=adapter.parse_file('东财.xlsx',encoded(raw),mapping=mapping)
    assert fixed['can_preview'] and fixed['audit']['ignored_rows']==1
    mapping['ignored_rows'][0]['row']=100
    assert 'unused_ignored_row' in {i['code'] for i in adapter.parse_file('东财.xlsx',encoded(raw),mapping=mapping)['issues']}


@pytest.mark.parametrize('missing', ['all','one'])
def test_unknown_fee_components_remain_unknown_never_reversed_from_net_cash(missing):
    row=deepcopy(BUY)
    for col in ([9,10,11,12] if missing=='all' else [12]):row[col]=''
    _,_,result=inspect_parse({'原表':[HEADER,row]})
    assert result['can_preview'] and result['payload']['fills'][0]['fees'] is None
    zero=deepcopy(BUY);zero[8]=-1000;zero[9]=0
    _,_,result=inspect_parse({'原表':[HEADER,zero]})
    assert result['payload']['fills'][0]['fees']==0


def test_cash_and_stock_events_preserve_explicit_zero_and_unknown_ipo_link():
    rows=[]
    for business,code,quantity,cash in [('银行转证券','-',0,10000),('红利入账','000636',100,10),('新股入账','688001',500,0),('配售缴款','787001',500,-2000)]:
        r=deepcopy(BUY);r[2],r[4],r[5],r[8]=code,business,quantity,cash;rows.append(r)
    _,_,result=inspect_parse({'原表':[HEADER,*rows]})
    assert result['can_preview'] and len(result['payload']['financial_events'])==4
    financial=result['payload']['financial_events']
    assert financial[0]['code']=='CASH' and financial[0]['quantity']==0
    assert financial[-1]['kind']=='ipo_payment' and financial[-1]['linked_ticker'] is None
    rows[0][5]=''
    assert not inspect_parse({'原表':[HEADER,*rows]})[2]['can_preview']


def test_csv_gb18030_directional_cash_and_date_default_are_explicit():
    text='证券代码,证券名称,买卖类别,成交数量,成交价格,成交金额,发生金额,币种\n000636,风华高科,买入,100,10,1000,1001,人民币\n'
    raw=text.encode('gb18030');inspection=adapter.inspect_file('原表.csv',encoded(raw))
    assert inspection['encoding']=='gb18030'
    mapping=inspection['suggested_mapping'];table=mapping['tables'][0]
    assert not adapter.parse_file('原表.csv',encoded(raw),mapping=mapping)['can_preview']
    table.update(date_default='2026-10-19',cash_sign='directional_magnitude')
    result=adapter.parse_file('原表.csv',encoded(raw),mapping=mapping)
    assert result['can_preview'] and result['payload']['fills'][0]['net_cash']==-1001
    assert result['payload']['fills'][0]['time_precision']=='date_only'
    text=text.replace('1001','-1001')
    assert not adapter.parse_file('原表.csv',encoded(text.encode('gb18030')),mapping=mapping)['can_preview']


def test_cached_formula_is_never_used_and_summary_exclusion_is_audited():
    row=deepcopy(BUY);row[7]=('100*10',1000)
    raw=workbook({'原表':[HEADER,row]})
    mapping=adapter.inspect_file('东财.xlsx',encoded(raw))['suggested_mapping']
    assert mapping['tables']==[]  # inspector does not recommend formula facts
    columns={k:i for k,i in zip(['date','statement_event_time','code','name','kind','quantity','price','amount','net_cash','commission','regulatory_fee','stamp_tax','transfer_fee','quantity_after','currency'],range(1,16))}
    mapping={'tables':[{'sheet':'原表','header_row':1,'columns':columns,'cash_sign':'signed','currency_default':'CNY'}]}
    result=adapter.parse_file('东财.xlsx',encoded(raw),mapping=mapping)
    assert result['payload'] is None and result['issues'][0]['code']=='formula_not_supported'


def test_numeric_broker_identifier_or_conflicting_execution_time_is_unresolved():
    row=deepcopy(EXEC);row[8]=123
    assert not inspect_parse({'实际成交':[EXEC_HEADER,row]})[2]['can_preview']
    second=deepcopy(EXEC);second[1]='10:01:01'
    raw,mapping,_=inspect_parse({'实际成交':[EXEC_HEADER,EXEC],'旁证':[EXEC_HEADER,second]})
    mapping['tables'][0]['role']='events';mapping['tables'][1]['role']='corroborating_executions'
    result=adapter.parse_file('东财.xlsx',encoded(raw),mapping=mapping)
    assert result['payload'] is None and 'corroboration_conflict' in {i['code'] for i in result['issues']}


def test_inspection_masks_account_columns_without_mutating_original_archive():
    raw,mapping,result=inspect_parse({'原表':[HEADER+['资金账号'],BUY+['01234567890123456789']]})
    inspection=adapter.inspect_file('东财.xlsx',encoded(raw))
    assert inspection['sheets'][0]['row_previews'][1]['cells'][-1]=='[账户字段已脱敏]'
    assert b'01234567890123456789' in zipfile.ZipFile(BytesIO(raw)).read('xl/worksheets/sheet1.xml')
    assert base64.b64decode(result['payload']['sources'][0]['content'])==raw
    with pytest.raises(TradeJournalError,match='alias'):
        adapter.parse_file('东财.xlsx',encoded(raw),mapping=mapping,account_alias='012345678901')
    preamble=workbook({'原表':[['资金账号：123456，客户姓名：测试用户'],HEADER,BUY]})
    inspected=adapter.inspect_file('原件.xlsx',encoded(preamble))
    assert inspected['sheets'][0]['row_previews'][0]['cells'][0]=='[账户字段已脱敏]'


@pytest.mark.parametrize('value', ['NaN','inf','1e99999999','1,23'])
def test_nonfinite_or_ambiguous_financial_numbers_are_issues(value):
    row=deepcopy(BUY);row[7]=value
    result=inspect_parse({'原表':[HEADER,row]})[2]
    assert not result['can_preview'] and result['payload'] is None


@pytest.mark.parametrize('kwargs', [{'row_start':0},{'row_start':True},{'row_limit':201},{'row_limit':'100'},{'sheet':'不存在'}])
def test_inspection_pagination_validation_is_domain_error(kwargs):
    with pytest.raises(adapter.ImportFileError):adapter.inspect_file('test.xlsx',encoded(workbook({'原表':[HEADER,BUY]})),**kwargs)


@pytest.mark.parametrize('change', [lambda m:m.update(ignored_rows={}),lambda m:m.update(ignored_rows=[{'sheet':'原表','row':2,'reason':{}}]),lambda m:m['tables'][0].update(sheet=[]),lambda m:m['tables'][0].update(kind_map={'x':[]}),lambda m:m.update(corroboration_pairs=[{}])])
def test_malformed_mapping_is_422_compatible_not_generic_exception(change):
    raw=workbook({'原表':[HEADER,BUY]});mapping=adapter.inspect_file('test.xlsx',encoded(raw))['suggested_mapping'];change(mapping)
    with pytest.raises(adapter.ImportFileError):adapter.parse_file('test.xlsx',encoded(raw),mapping=mapping)


@pytest.mark.parametrize('extra', [{'xl/vbaProject.bin':'macro'},{'xl/externalLinks/externalLink1.xml':'x'},{'evil.xml':'<!DOCTYPE a [<!ENTITY x "x">]><a/>'},{'evil.rels':'<Relationship TargetMode="External"/>'},{'../bad.xml':'x'}])
def test_unsafe_archive_members_rejected_without_opening_or_execution(extra):
    with pytest.raises(adapter.ImportFileError):adapter.inspect_file('test.xlsx',encoded(workbook({'原表':[HEADER,BUY]},extra=extra)))


def test_raw_and_expanded_budgets_and_invalid_file_types(monkeypatch):
    with pytest.raises(adapter.ImportFileError):adapter.inspect_file('a.xlsx','not-base64')
    with pytest.raises(adapter.ImportFileError):adapter.inspect_file('a.csv',encoded(b'<html>fake xls</html>'))
    with pytest.raises(adapter.ImportFileError):adapter.inspect_file('a.exe',encoded(b'abc'))
    monkeypatch.setitem(adapter.LIMITS,'max_file_bytes',8)
    with pytest.raises(adapter.ImportFileError):adapter.inspect_file('a.csv',encoded(b'123456789'))
    monkeypatch.setitem(adapter.LIMITS,'max_file_bytes',8*1024*1024)
    monkeypatch.setitem(adapter.LIMITS,'max_uncompressed_bytes',200)
    with pytest.raises(adapter.ImportFileError):adapter.inspect_file('a.xlsx',encoded(workbook({'原表':[HEADER,BUY]})))


@pytest.mark.parametrize('dimension',['A1:XFD1048576','A1:A1'])
def test_sparse_or_understated_dimension_is_not_materialized_or_allowed_to_hide_rows(dimension):
    initial=workbook({'原表':[HEADER,BUY]})
    with zipfile.ZipFile(BytesIO(initial)) as z:xml=z.read('xl/worksheets/sheet1.xml').decode()
    raw=workbook({'原表':[HEADER,BUY]},extra={'xl/worksheets/sheet1.xml':xml.replace('<sheetData>',f'<dimension ref="{dimension}"/><sheetData>')})
    inspection=adapter.inspect_file('sparse.xlsx',encoded(raw))
    assert inspection['sheets'][0]['row_count']==2
    parsed=adapter.parse_file('sparse.xlsx',encoded(raw),mapping=inspection['suggested_mapping'])
    assert parsed['can_preview'] and len(parsed['payload']['fills'])==1


def test_physical_xml_cell_budget_is_checked_before_openpyxl_allocation(monkeypatch):
    import openpyxl
    calls=[]
    monkeypatch.setitem(adapter.LIMITS,'max_cells',len(HEADER)+len(BUY)-1)
    monkeypatch.setattr(openpyxl,'load_workbook',lambda *a,**kw:calls.append(True))
    with pytest.raises(adapter.ImportFileError,match='单元格'):adapter.inspect_file('a.xlsx',encoded(workbook({'原表':[HEADER,BUY]})))
    assert calls==[]


def test_csv_cell_reservation_precedes_building_cell_dictionaries(monkeypatch):
    calls=[];original=adapter._bounded_row
    def capture(cells):calls.append(len(cells));return original(cells)
    monkeypatch.setitem(adapter.LIMITS,'max_cells',20)
    monkeypatch.setattr(adapter,'_bounded_row',capture)
    text=','.join(HEADER)+'\n'+','.join(str(v) for v in BUY)+'\n'
    with pytest.raises(adapter.ImportFileError,match='单元格'):adapter.inspect_file('a.csv',encoded(text.encode()))
    assert calls==[15]


def test_unavailable_xls_dependency_rejects_instead_of_guessing_html(monkeypatch):
    import builtins
    original=builtins.__import__
    def no_xlrd(name,*args,**kwargs):
        if name=='xlrd':raise ImportError('unavailable')
        return original(name,*args,**kwargs)
    monkeypatch.setattr(builtins,'__import__',no_xlrd)
    with pytest.raises(adapter.ImportFileError,match='依赖'):adapter.inspect_file('a.xls',encoded(b'oldbinary'))


@pytest.mark.parametrize('relative,count,financial,joins', [
    ('outputs/01a14866-b9df-7663-a58b-dccf6cb44cd5/东财交割单_20260701-20261017.xlsx',462,11,0),
    ('outputs/01a14866-b9df-7663-a58b-dccf6cb44cd5/交割单_20261027/东财交割单_20261027.xlsx',11,0,11)])
def test_available_actual_workbook_families(relative,count,financial,joins):
    path=Path(__file__).resolve().parents[1]/relative
    if not path.is_file():pytest.skip('private broker source fixture is not distributed')
    raw=path.read_bytes();mapping=adapter.inspect_file(path.name,encoded(raw))['suggested_mapping']
    for item in mapping['ignored_sheets']:item['reason']='明确排除派生汇总，仅取原始表'
    result=adapter.parse_file(path.name,encoded(raw),mapping=mapping)
    assert result['can_preview'],result['issues']
    assert (result['audit']['parsed_fill_rows'],result['audit']['financial_event_rows'],result['audit']['corroborated_rows'])==(count,financial,joins)
