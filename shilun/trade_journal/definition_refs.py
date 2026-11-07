"""Resolve static terms and exact user-card versions through their owners."""
from copy import deepcopy

from .definitions import DEFINITIONS_VERSION, validate_definition_references


def resolve_definition_references(store, references, *, expected_category=None):
    from .mode_cards import ModeCardService
    references = [references] if isinstance(references,(str,dict)) else list(references or [])
    resolved, errors, versions = [], [], {}
    for ref in references:
        identifier = ref if isinstance(ref,str) else ref.get('id') if isinstance(ref,dict) else None
        if isinstance(identifier,str) and identifier.startswith('mode.user.'):
            if not isinstance(ref,dict) or not ref.get('rule_version'):
                errors.append({'id':identifier,'code':'missing_user_mode_version','message':'用户模式必须指定确切rule_version，不能自动采用最新模式。'})
                continue
            if (expected_category and expected_category!='mode') or (ref.get('category') and ref['category']!='mode'):
                errors.append({'id':identifier,'code':'category_mismatch','message':'用户模式不能当触发形态或管理动作。'})
                continue
            try:
                resolved.append(ModeCardService(store).get_definition_snapshot(identifier,rule_version=ref['rule_version']))
            except ValueError as error:
                errors.append({'id':identifier,'code':'user_mode_unavailable','message':str(error)})
        else:
            result=validate_definition_references([ref],expected_category=expected_category)
            resolved.extend(result['resolved']);errors.extend(result['errors'])
    for row in resolved:
        versions.setdefault(row['id'],set()).add(row['rule_version'])
    for identifier, choices in versions.items():
        if len(choices)>1:
            errors.append({'id':identifier,'code':'ambiguous_definition_version',
                'message':'同一术语不能同时绑定不同版本；请明确本次采用的确切版本。'})
    unique={ (r['id'],r['rule_version']):r for r in resolved }
    return {'valid':not errors,'version':DEFINITIONS_VERSION,'resolved':deepcopy(list(unique.values())),'errors':errors}
