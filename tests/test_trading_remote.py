"""Remote Mongo reads must be bounded and keep write-side index safeguards."""
from types import SimpleNamespace
from unittest.mock import Mock, patch

from pymongo.errors import NetworkTimeout

from shilun.api import _open_holdings_store, create_app, get_trading_executions, ui_router
from shilun.trading.market_facts import _latest, _sector_cache, SECTOR_MONITOR_PROJECTION, SELECTION_POOL_PROJECTION
from test_review_remote import LocalASGIClient


def test_holdings_read_budget_skips_ddl_but_writes_keep_indexes():
    with patch('shilun.api.load_config', return_value=SimpleNamespace(mongo_uri='mongodb://test', mongo_db='test')), \
         patch('shilun.common.db.MongoSnapshotStore') as factory:
        _open_holdings_store(read_only=True)
        factory.assert_called_with('mongodb://test', 'test', socket_timeout_ms=30000, initialize_indexes=False)
        _open_holdings_store()
        factory.assert_called_with('mongodb://test', 'test', socket_timeout_ms=30000, initialize_indexes=True)


def test_read_routes_do_not_initialize_indexes():
    import ast
    import inspect
    for route in ui_router.routes:
        endpoint = route.endpoint
        source = inspect.getsource(endpoint)
        if 'GET' not in route.methods or '_open_holdings_store(' not in source:
            continue
        calls = [n for n in ast.walk(ast.parse(source)) if isinstance(n, ast.Call)
                 and isinstance(n.func, ast.Name) and n.func.id == '_open_holdings_store']
        assert all(any(k.arg == 'read_only' and isinstance(k.value, ast.Constant) and k.value.value is True
                       for k in call.keywords) for call in calls), endpoint.__name__


def test_latest_missing_snapshot_is_not_requested_twice():
    collection = Mock()
    collection.find_one.return_value = None
    assert _latest(collection, {'analysis_date': '2026-09-22'}, 'analysis_date', SELECTION_POOL_PROJECTION) is None
    collection.find.assert_not_called()
    assert collection.find_one.call_args.args[1] == SELECTION_POOL_PROJECTION


def test_sector_monitor_only_reads_membership_and_stops_at_current_snapshot():
    store = Mock()
    store.collection.return_value.find_one.return_value = {'analysis_date': '2026-09-22', 'payload': {'top_sectors': []}}
    assert _sector_cache(store, '20260922')['analysis_date'] == '2026-09-22'
    store.collection.return_value.find_one.assert_called_once()
    assert store.collection.return_value.find_one.call_args.args[1] == SECTOR_MONITOR_PROJECTION
    assert 'payload.candidates.ticker' in SECTOR_MONITOR_PROJECTION
    assert 'payload' not in SECTOR_MONITOR_PROJECTION


def test_execution_ledger_limits_on_server_and_skips_name_lookup_when_named():
    store = Mock()
    collection = store.collection.return_value
    collection.find.return_value.sort.return_value.limit.return_value = [
        {'ticker': '000017.SZ', 'name': '深中华A', 'executed_at': '2026-09-22T10:00:00'},
    ]
    with patch('shilun.api._open_holdings_store', return_value=store) as opener:
        result = get_trading_executions('000017.SZ', limit=10)
    assert result['count'] == 1
    opener.assert_called_once_with(read_only=True)
    collection.find.assert_called_once_with({'ticker': '000017.SZ'}, {'_id': 0})
    collection.find.return_value.sort.assert_called_once_with('executed_at', -1)
    collection.find.return_value.sort.return_value.limit.assert_called_once_with(10)
    store.close.assert_called_once()


def test_database_read_failure_is_actionable_json_and_redacts_driver_details():
    store = Mock()
    store.collection.side_effect = NetworkTimeout('secret-example-credential')
    with patch('shilun.api.load_config', return_value=SimpleNamespace(auth_required=False)), \
         patch('shilun.api._open_holdings_store', return_value=store):
        response = LocalASGIClient(create_app()).get('/api/v1/trading/executions')
    assert response.status_code == 503
    assert '数据库' in response.json()['detail']
    assert 'secret-example-credential' not in response.text
    store.close.assert_called_once()


def test_event_projection_preserves_display_semantics_without_heavy_evidence():
    from copy import deepcopy
    from shilun.trading.workbench import DECISION_DISPLAY_PROJECTION, normalize_decision_event
    from test_trading_workbench import _decision_event
    event = _decision_event()
    event['facts_snapshot'] = {'large_history': ['unused'] * 1000}
    event['decision']['current_scenario'] = {'scenario_id': 'E3', 'title': '支撑反转', 'state': 'confirmed',
                                            'traces': ['unused'] * 1000}
    event['decision']['candidate_scenarios'] = ['unused'] * 1000
    projected = {}
    for path, included in DECISION_DISPLAY_PROJECTION.items():
        if not included:
            continue
        src, dest = event, projected
        parts = path.split('.')
        for part in parts[:-1]:
            src = src.get(part, {})
            dest = dest.setdefault(part, {})
        if parts[-1] in src:
            dest[parts[-1]] = deepcopy(src[parts[-1]])
    assert normalize_decision_event(projected) == normalize_decision_event(event)
    assert 'facts_snapshot' not in projected
    assert 'traces' not in projected['decision']['current_scenario']
    assert 'candidate_scenarios' not in projected['decision']
