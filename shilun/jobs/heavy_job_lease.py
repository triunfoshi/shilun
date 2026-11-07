"""One renewable Mongo lease for bulk analysis and data materialization."""
from datetime import datetime, timedelta, timezone
import threading

from pymongo.errors import DuplicateKeyError


LEASE_ID = 'daily_data_pipeline'  # Keep the existing cross-host exclusion key.
LEASE_TTL = timedelta(minutes=3)
HEARTBEAT_SECONDS = 30


class PipelineBusy(RuntimeError):
    pass


class LeaseLost(RuntimeError):
    pass


def utc_now():
    return datetime.now(timezone.utc)


def as_utc(value):
    if isinstance(value, str):
        value = datetime.fromisoformat(value)
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def acquire_lease(collection, owner, now):
    """Competing hosts serialize through the pre-existing unique Mongo _id."""
    try:
        result = collection.update_one(
            {'_id': LEASE_ID, 'expires_at': {'$lte': now}},
            {'$set': {'owner': owner, 'expires_at': now + LEASE_TTL}}, upsert=True)
    except DuplicateKeyError as error:
        raise PipelineBusy('Another heavy job is running') from error
    if not (result.modified_count or result.upserted_id):
        raise PipelineBusy('Another heavy job is running')


class HeavyJobLease:
    def __init__(self, collection, owner, *, now_fn=utc_now, heartbeat_seconds=HEARTBEAT_SECONDS):
        self.collection, self.owner = collection, owner
        self.now_fn, self.heartbeat_seconds = now_fn, heartbeat_seconds
        self._stop = threading.Event()
        self._lost = threading.Event()
        self._pulse = None

    def start(self):
        acquire_lease(self.collection, self.owner, self.now_fn())
        self._pulse = threading.Thread(target=self._heartbeat, daemon=True)
        self._pulse.start()
        return self

    def renew(self):
        now = self.now_fn()
        result = self.collection.update_one(
            {'_id': LEASE_ID, 'owner': self.owner, 'expires_at': {'$gt': now}},
            {'$set': {'heartbeat_at': now, 'expires_at': now + LEASE_TTL}})
        if not getattr(result, 'matched_count', result.modified_count):
            self._lost.set()
            raise LeaseLost('Heavy-job ownership expired or changed')

    def _heartbeat(self):
        while not self._stop.wait(self.heartbeat_seconds):
            try:
                self.renew()
            except LeaseLost:
                return
            except Exception:
                # A transient network error may recover; publication still
                # requires a fresh successful ownership read.
                continue

    def assert_owned(self):
        row = self.collection.find_one({'_id': LEASE_ID, 'owner': self.owner})
        if (self._lost.is_set() or not row or not row.get('expires_at')
                or as_utc(row['expires_at']) <= self.now_fn()):
            self._lost.set()
            raise LeaseLost('Heavy-job ownership expired or changed')

    def close(self):
        self._stop.set()
        if self._pulse:
            self._pulse.join(timeout=2)
        # A stale worker must never release its successor's lease.
        self.collection.delete_one({'_id': LEASE_ID, 'owner': self.owner})
