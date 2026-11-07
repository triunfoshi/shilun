"""Discover audited backend queries from a generated, read-only JSON catalog.

Runtime discovery never imports the API, opens a database, or probes a service.
Regenerate through scripts/build_feishu_readonly_catalog.py after route audits.
"""
from __future__ import annotations

from copy import deepcopy
from functools import lru_cache
import json
from pathlib import Path
import re
from typing import Any

CATALOG_PATH = Path(__file__).resolve().parent / 'config/feishu_readonly_catalog.json'
_NORMALIZE = re.compile(r'[^a-z0-9_\u3400-\u9fff]+')
_CHINESE = re.compile(r'[\u3400-\u9fff]+')
_WORDS = re.compile(r'[a-z0-9_]+')


@lru_cache(maxsize=1)
def _catalog() -> dict:
    try:
        result = json.loads(CATALOG_PATH.read_text(encoding='utf-8'))
        if result.get('version') != 1 or not isinstance(result.get('queries'), list) or not isinstance(result.get('modules'), list):
            raise ValueError
        identifiers = [item['id'] for item in result['queries']]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError
        return result
    except (OSError, ValueError, TypeError, KeyError):
        raise ValueError('后台只读目录不可用，请检查已生成的目录文件。') from None


def get_query(query_id: str) -> dict:
    """Return an isolated audited entry; denied paths are never query IDs."""
    if not isinstance(query_id, str) or not 1 <= len(query_id) <= 160:
        raise ValueError('无效的只读查询标识。')
    entry = next((item for item in _catalog()['queries'] if item['id'] == query_id), None)
    if entry is None:
        raise ValueError('未知或未获只读授权的查询。')
    return deepcopy(entry)


def list_modules() -> dict:
    catalog = _catalog()
    return {'modules': deepcopy(catalog['modules']), 'count': len(catalog['modules']),
            'query_count': len(catalog['queries']), 'coverage': deepcopy(catalog['coverage'])}


def _normalize(text: str) -> str:
    return _NORMALIZE.sub('', text.lower())


def _terms(text: str) -> set[str]:
    result = set(_WORDS.findall(text.lower()))
    for part in _CHINESE.findall(text):
        result.update(part[index:index+2] for index in range(len(part)-1))
        if len(part) == 1:
            result.add(part)
    return result


def _score(entry: dict, query: str) -> int:
    title = ' '.join((entry['summary'], entry['module_name']))
    identity = ' '.join((entry['id'], entry['path'], entry['module']))
    text = title+' '+identity+' '+entry['description']
    normalized = _normalize(query)
    score = 40 if normalized and normalized in _normalize(title) else 0
    if normalized and normalized in _normalize(identity):
        score += 30
    for term in _terms(query):
        if term in title.lower():
            score += 6
        elif term in identity.lower():
            score += 4
        elif term in text.lower():
            score += 1
    return score


def search_queries(module: str = '', query: str = '', offset: int = 0, limit: int = 12) -> dict:
    """Find Chinese/English query descriptions with bounded stable pagination."""
    if (not isinstance(module, str) or len(module) > 100
            or not isinstance(query, str) or len(query) > 256
            or type(offset) is not int or not 0 <= offset <= 10000
            or type(limit) is not int or not 1 <= limit <= 50):
        raise ValueError('目录查询参数无效：offset须为非负整数，limit须为1至50。')
    catalog = _catalog()
    module, query = module.strip(), query.strip()
    if module:
        selected = next((item for item in catalog['modules'] if module in (item['id'], item['name'])), None)
        if selected is None:
            raise ValueError('未知后台模块，请先读取模块目录。')
        module = selected['id']
    candidates = [entry for entry in catalog['queries'] if not module or entry['module'] == module]
    if query:
        scored = [(entry, _score(entry, query)) for entry in candidates]
        candidates = [entry for entry, score in sorted(scored, key=lambda pair: (-pair[1], pair[0]['id'])) if score > 0]
    total = len(candidates)
    return {'queries': deepcopy(candidates[offset:offset+limit]), 'module': module, 'query': query,
            'offset': offset, 'limit': limit, 'count': len(candidates[offset:offset+limit]),
            'total': total, 'has_more': offset+limit < total}
