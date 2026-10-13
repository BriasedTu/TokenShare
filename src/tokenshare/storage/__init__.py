"""Local artifact, event ledger, and SQLite index storage."""

from tokenshare.storage.artifacts import ArtifactStore
from tokenshare.storage.events import EventLedger, EventType, LedgerEvent


def __getattr__(name: str):
    if name == "SQLiteMaterializedIndex":
        from tokenshare.storage.sqlite_index import SQLiteMaterializedIndex

        return SQLiteMaterializedIndex
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

__all__ = [
    "ArtifactStore",
    "EventLedger",
    "EventType",
    "LedgerEvent",
    "SQLiteMaterializedIndex",
]
