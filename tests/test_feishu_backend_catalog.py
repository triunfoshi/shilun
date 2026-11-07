"""Full-route coverage, schema safety, and bounded catalog discovery checks."""
from copy import deepcopy
import json
import unittest
from unittest.mock import patch

from jsonschema import Draft202012Validator, FormatChecker
from scripts import build_feishu_readonly_catalog as builder
from shilun import feishu_backend_catalog as catalog


def has_reference(item):
    if isinstance(item, dict):
        return '$ref' in item or any(has_reference(value) for value in item.values())
    return isinstance(item, list) and any(has_reference(value) for value in item)


class CatalogContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.document, cls.metadata = builder.load_openapi()
        cls.audits = [row for path in builder.AUDITS for row in json.loads(path.read_text())['routes']]
        cls.generated = builder.build_catalog(cls.document, cls.metadata, cls.audits)

    def test_every_current_get_and_exactly_seven_preview_posts_are_covered(self):
        expected_gets = {path for path, operations in self.document['paths'].items()
                         if path.startswith('/api/v1/') and 'get' in operations}
        queries = self.generated['queries']
        self.assertEqual(expected_gets, {item['path'] for item in queries if item['method'] == 'GET'})
        expected_posts = {row['path'] for row in self.audits if row['method'] == 'POST' and row['read_only']}
        self.assertEqual(7, len(expected_posts))
        self.assertEqual(expected_posts, {item['path'] for item in queries if item['method'] == 'POST'})
        coverage = self.generated['coverage']
        self.assertEqual(158, coverage['all_operations'])
        self.assertEqual(97, coverage['read_only_get'])
        self.assertEqual(158, coverage['classified_operations'])
        self.assertEqual([], coverage['unclassified'])

    def test_generated_catalog_matches_the_checked_in_artifact(self):
        self.assertEqual(self.generated, json.loads(catalog.CATALOG_PATH.read_text()))

    def test_unknown_get_fails_build_instead_of_being_implicitly_exposed(self):
        document = deepcopy(self.document)
        document['paths']['/api/v1/new-unaudited-query'] = {'get': {'operationId': 'new_unaudited_query'}}
        with self.assertRaisesRegex(ValueError, 'Unreviewed GET'):
            builder.build_catalog(document, self.metadata, self.audits)

    def test_unreviewed_write_is_classified_as_denied(self):
        document = deepcopy(self.document)
        document['paths']['/api/v1/new-write'] = {'post': {'operationId': 'new_write'}}
        built = builder.build_catalog(document, self.metadata, self.audits)
        self.assertNotIn('/api/v1/new-write', {item['path'] for item in built['queries']})
        self.assertIn('/api/v1/new-write', {item['path'] for item in built['denied']})

    def test_read_only_compatibility_flags_are_mandatory_and_non_overridable(self):
        by_id = {item['id']: item for item in self.generated['queries']}
        for identifier in ('market_intraday', 'market_permission', 'market_daily_leaders',
                           'search_stocks', 'stock_panel', 'get_market_facts'):
            item = by_id[identifier]
            self.assertIs(item['fixed_parameters']['read_only'], True)
            self.assertIs(item['parameter_schema']['properties']['read_only']['const'], True)
        audits = deepcopy(self.audits)
        row = next(row for row in audits if row['method'] == 'GET' and row['path'] == '/api/v1/market/intraday')
        row.pop('fixed_params')
        with self.assertRaisesRegex(ValueError, 'compatibility flag'):
            builder.build_catalog(self.document, self.metadata, audits)

    def test_all_schemas_are_local_expanded_and_validator_compatible(self):
        for item in self.generated['queries']:
            with self.subTest(query=item['id']):
                self.assertFalse(has_reference(item['parameter_schema']))
                Draft202012Validator.check_schema(item['parameter_schema'])
                self.assertIs(item['parameter_schema']['additionalProperties'], False)
                if item['body_schema']:
                    self.assertFalse(has_reference(item['body_schema']))
                    Draft202012Validator.check_schema(item['body_schema'])

    def test_candidate_query_requires_explicit_known_strategy_without_changing_ui_default(self):
        item = next(item for item in self.generated['queries'] if item['id'] == 'market_candidates')
        validator = Draft202012Validator(item['parameter_schema'], format_checker=FormatChecker())
        self.assertFalse(validator.is_valid({'date': '2026-11-04'}))
        for identifier in builder.CANDIDATE_STRATEGIES:
            self.assertTrue(validator.is_valid({'date': '2026-11-04', 'strategy_id': identifier}))
        for identifier in ('周K突破套利', 'wk_breakout', 'unknown_strategy'):
            self.assertFalse(validator.is_valid({'date': '2026-11-04', 'strategy_id': identifier}))
        strategy = next(p for p in item['parameters'] if p['name'] == 'strategy_id')
        self.assertTrue(strategy['required'])
        self.assertNotIn('default', strategy['schema'])
        # Tool contract is stricter; existing UI/API behavior remains compatible.
        api_strategy = next(p for p in self.document['paths']['/api/v1/market/candidates']['get']['parameters']
                            if p['name'] == 'strategy_id')
        self.assertEqual(api_strategy['schema']['default'], 'ma5_v0_2')
        self.assertFalse(api_strategy.get('required'))

    def test_candidate_strategy_ids_match_the_visible_selection_controls(self):
        from pathlib import Path
        import re
        html = (Path(__file__).resolve().parents[1]/'shilun/static/index.html').read_text()
        selector = re.search(r'<select id="candidateStrategySelect"[^>]*>(.*?)</select>', html, re.S).group(1)
        identifiers = set(re.findall(r'<option value="([^"]+)"', selector))
        self.assertEqual(identifiers, set(builder.CANDIDATE_STRATEGIES))
        item = next(item for item in self.generated['queries'] if item['id'] == 'market_candidates')
        self.assertIn('周K突破战法', item['description'])
        self.assertIn('weekly_breakout', item['description'])

    def test_actual_gateway_never_sends_an_implicit_ma5_or_display_name_query(self):
        from types import SimpleNamespace
        from unittest.mock import Mock
        from shilun import feishu_backend_gateway as gateway
        session = Mock()
        session.request.return_value = SimpleNamespace(status_code=200, headers={}, close=lambda: None,
            iter_content=lambda **kwargs: iter([b'{"status":"ok","candidates":[]}']))
        with patch.object(gateway, '_configured_base', return_value='http://backend.test'):
            client = gateway.BackendGateway(session=session,
                config=SimpleNamespace(auth_username=None, auth_password=None))
            for parameters in ({'date': '2026-11-04'},
                               {'date': '2026-11-04', 'strategy_id': '周K突破套利'},
                               {'date': '2026-11-04', 'strategy_id': 'wk_breakout'}):
                self.assertEqual('error', client.query('market_candidates', parameters)['status'])
            session.request.assert_not_called()
            result = client.query('market_candidates', {'date': '2026-11-04', 'strategy_id': 'weekly_breakout'})
        self.assertEqual('ok', result['status'])
        session.request.assert_called_once()
        self.assertEqual('weekly_breakout', session.request.call_args.kwargs['params']['strategy_id'])

    def test_local_nullable_schema_is_preserved_but_remote_and_cycles_fail(self):
        document = {'components': {'schemas': {
            'Preview': {'type': 'object', 'properties': {'price': {'anyOf': [{'type': 'number'}, {'type': 'null'}]}}},
            'Cycle': {'type': 'object', 'properties': {'next': {'$ref': '#/components/schemas/Cycle'}}},
        }}}
        schema = builder.resolve_schema({'$ref': '#/components/schemas/Preview'}, document)
        self.assertTrue(Draft202012Validator(schema).is_valid({'price': None}))
        self.assertTrue(Draft202012Validator(schema).is_valid({'price': 1.2}))
        self.assertFalse(Draft202012Validator(schema).is_valid({'price': 'text'}))
        for reference in ('https://example.invalid/schema', '#/components/schemas/Cycle', '#/components/schemas/Missing'):
            with self.subTest(reference=reference), self.assertRaises(ValueError):
                builder.resolve_schema({'$ref': reference}, document)

    def test_iso_dates_validate_calendar_days_without_restricting_theme_codes(self):
        item = next(item for item in self.generated['queries'] if item['id'] == 'get_topic_flow')
        validator = Draft202012Validator(item['parameter_schema'], format_checker=FormatChecker())
        self.assertTrue(validator.is_valid({'date': '2026-11-03', 'code': '123456.TI'}))
        self.assertTrue(validator.is_valid({'date': '2026-11-03', 'code': '123456.SI'}))
        self.assertFalse(validator.is_valid({'date': '2026-02-30', 'code': '123456.TI'}))
        self.assertFalse(validator.is_valid({'date': '20261103', 'code': '123456.TI'}))
        self.assertFalse(validator.is_valid({'date': '2026-11-03', 'code': '123456.SH'}))


class DiscoveryTests(unittest.TestCase):
    def test_modules_and_paginated_queries_cover_every_allowed_id_once(self):
        modules = catalog.list_modules()
        self.assertTrue(15 <= modules['count'] <= 25)
        self.assertEqual(104, sum(item['query_count'] for item in modules['modules']))
        identifiers = []
        offset = 0
        while True:
            page = catalog.search_queries(offset=offset, limit=7)
            identifiers.extend(item['id'] for item in page['queries'])
            if not page['has_more']:
                break
            offset += 7
        self.assertEqual(104, len(identifiers))
        self.assertEqual(104, len(set(identifiers)))
        self.assertEqual(0, catalog.search_queries(offset=1000)['count'])

    def test_chinese_natural_phrases_and_english_query_identifiers_find_the_right_api(self):
        self.assertEqual('get_board_effects', catalog.search_queries(query='最近30天最高板期望收益')['queries'][0]['id'])
        self.assertEqual('stock_panel', catalog.search_queries(query='stock panel')['queries'][0]['id'])
        selected = catalog.search_queries(module='持仓管理')
        self.assertTrue(selected['queries'])
        self.assertTrue(all(item['module'] == 'holdings' for item in selected['queries']))
        self.assertEqual('market_candidates', catalog.search_queries(query='周K突破套利候选池')['queries'][0]['id'])

    def test_results_are_copies_and_writes_or_arbitrary_paths_have_no_query_identity(self):
        entry = catalog.get_query('market_intraday')
        entry['fixed_parameters']['read_only'] = False
        self.assertIs(catalog.get_query('market_intraday')['fixed_parameters']['read_only'], True)
        first = catalog.list_modules()
        first['modules'][0]['name'] = 'mutated'
        self.assertNotEqual('mutated', catalog.list_modules()['modules'][0]['name'])
        for identifier in ('refresh_dashboard', '/api/v1/review/dashboard', 'https://example.invalid', ''):
            with self.subTest(identifier=identifier), self.assertRaises(ValueError):
                catalog.get_query(identifier)

    def test_invalid_discovery_parameters_are_rejected(self):
        invalid = ({'module': 'nonexistent'}, {'module': None}, {'query': 'x'*257}, {'query': None},
                   {'offset': True}, {'offset': -1}, {'offset': 10001}, {'limit': True}, {'limit': 0}, {'limit': 51})
        for arguments in invalid:
            with self.subTest(arguments=arguments), self.assertRaises(ValueError):
                catalog.search_queries(**arguments)


if __name__ == '__main__':
    unittest.main()
