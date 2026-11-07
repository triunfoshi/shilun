from copy import deepcopy
from types import SimpleNamespace

from test_pipeline_state import MemoryCollection


class Collection(MemoryCollection):
    def update_one(self, query, update, upsert=False):
        super().update_one(query, update, upsert=upsert)
        return SimpleNamespace(modified_count=1, upserted_id=None)

    def delete_one(self, query):
        self.rows.pop(query['_id'], None)


class Store:
    def __init__(self):
        self.collections, self.states = {}, {}

    def collection(self, name):
        return self.collections.setdefault(name, Collection())

    def find_sync_state(self, *, dataset, scope):
        return deepcopy(self.states.get((dataset, scope)))

    def upsert_sync_state(self, payload):
        key = (payload['dataset'], payload['scope'])
        self.states[key] = {**self.states.get(key, {}), **deepcopy(payload)}
