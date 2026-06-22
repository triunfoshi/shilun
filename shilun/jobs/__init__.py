"""Background jobs.

M5 减法说明：避免包入口提前导入所有 job，减少 CLI runpy 警告和
graphify 的伪连接；保留 `from shilun.jobs import SnapshotJob` 兼容写法。
"""

__all__ = [
    "CandidatePoolJob",
    "CandidatePoolRequest",
    "CandidatePoolResult",
    "SnapshotJob",
    "SnapshotJobRequest",
    "SnapshotJobResult",
    "TushareSyncJob",
    "TushareSyncRequest",
    "TushareSyncResult",
]


def __getattr__(name: str) -> object:
    if name in {"CandidatePoolJob", "CandidatePoolRequest", "CandidatePoolResult"}:
        from shilun.jobs.candidate_pool_job import CandidatePoolJob, CandidatePoolRequest, CandidatePoolResult

        return {
            "CandidatePoolJob": CandidatePoolJob,
            "CandidatePoolRequest": CandidatePoolRequest,
            "CandidatePoolResult": CandidatePoolResult,
        }[name]
    if name in {"SnapshotJob", "SnapshotJobRequest", "SnapshotJobResult"}:
        from shilun.jobs.snapshot_job import SnapshotJob, SnapshotJobRequest, SnapshotJobResult

        return {
            "SnapshotJob": SnapshotJob,
            "SnapshotJobRequest": SnapshotJobRequest,
            "SnapshotJobResult": SnapshotJobResult,
        }[name]
    if name in {"TushareSyncJob", "TushareSyncRequest", "TushareSyncResult"}:
        from shilun.jobs.tushare_sync_job import TushareSyncJob, TushareSyncRequest, TushareSyncResult

        return {
            "TushareSyncJob": TushareSyncJob,
            "TushareSyncRequest": TushareSyncRequest,
            "TushareSyncResult": TushareSyncResult,
        }[name]
    raise AttributeError(name)
