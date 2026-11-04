"""Batch preflight: group every input into payables and versions *before* any payment.

Pure function, no I/O. File order only chooses which of several byte-different but
semantically identical files is the canonical representation (harmless). It never decides
which *version* of an invoice is payable: that comes from explicit revision labels, and when
labels cannot order the versions, everything in the group is a conflict for a person.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from .config import Policy
from .identity import payable_key, semantic_fingerprint
from .schemas import InvoiceDraft
from .textnorm import revision_rank


class BatchKind(StrEnum):
    PROCESS = "process"
    DUPLICATE = "duplicate"
    SUPERSEDED = "superseded"
    CONFLICT = "conflict"


@dataclass(frozen=True)
class BatchEntry:
    file: str
    draft: InvoiceDraft


@dataclass(frozen=True)
class BatchDisposition:
    file: str
    kind: BatchKind
    related_file: str | None = None
    reason: str = ""


def plan_batch(entries: list[BatchEntry], policy: Policy) -> dict[str, BatchDisposition]:
    result: dict[str, BatchDisposition] = {}

    # 1. identical content in any format -> one canonical file, the rest are duplicates
    canonical: dict[str, BatchEntry] = {}
    for entry in sorted(entries, key=lambda e: e.file):
        fingerprint = semantic_fingerprint(entry.draft, policy)
        first = canonical.get(fingerprint)
        if first is None:
            canonical[fingerprint] = entry
        else:
            result[entry.file] = BatchDisposition(
                entry.file,
                BatchKind.DUPLICATE,
                first.file,
                f"Same invoice content as {first.file}.",
            )

    # 2. group the canonical files by payable identity
    groups: dict[object, list[BatchEntry]] = {}
    for entry in canonical.values():
        key = payable_key(entry.draft)
        groups.setdefault(key if key is not None else ("no-identity", entry.file), []).append(entry)

    for members in groups.values():
        if len(members) == 1:
            result[members[0].file] = BatchDisposition(members[0].file, BatchKind.PROCESS)
            continue
        ranks = {m.file: revision_rank(m.draft.revision) for m in members}
        values = list(ranks.values())
        unorderable = any(r is None for r in values) or len(set(values)) != len(values)
        if unorderable:
            names = ", ".join(sorted(ranks))
            for member in members:
                result[member.file] = BatchDisposition(
                    member.file,
                    BatchKind.CONFLICT,
                    None,
                    f"Versions of the same invoice cannot be ordered by revision label: {names}.",
                )
            continue
        newest = max(members, key=lambda m: ranks[m.file] or 0)
        for member in members:
            if member is newest:
                result[member.file] = BatchDisposition(member.file, BatchKind.PROCESS)
            else:
                result[member.file] = BatchDisposition(
                    member.file,
                    BatchKind.SUPERSEDED,
                    newest.file,
                    f"Replaced by revision {newest.draft.revision} in {newest.file}.",
                )
    return result
