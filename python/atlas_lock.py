"""Per-atlas lease lock in S3 — one writer per atlas at a time.

A session hydrates a workspace, runs, and writes back. Two sessions on one
atlas would each write back a tree derived from a state the other has since
changed. Concretely: a cascade refresh reads `roads`, a delta_upload applies an
edit to `roads` and archives the delta, then the cascade writes back its older
`roads` — and the edit is gone, with its delta already out of the pending
queue. Silently. One process writing to one disk hid this on the box; many
processes writing through S3 would not.

Mechanism: a small JSON object at ``{atlas}/lock``, created with a conditional
PUT (``If-None-Match: *``), so S3 itself arbitrates and exactly one creator
wins. The lease carries an expiry so a crashed holder cannot wedge an atlas for
good; an expired lease is taken over with ``If-Match`` on its ETag, so two
processes racing for the same stale lease still produce one winner.

A holder renews before expiry. Renewal is conditional on the lease's ETag, and
that conditional PUT — not anyone's clock — is what decides whether the lease
is still held: a holder whose lease was taken over finds out at its next
renewal. So a session must renew once more immediately before writing back.
That is what stops a process that stalled past its expiry from overwriting the
new holder's work.

Release is get-then-delete rather than a conditional delete, and deletes only
when the object is still ours and, by our clock, not yet expired. An unexpired
lease cannot be taken over, so nothing can slip in between the two calls.

No boto3 import: every function takes a client, so tests use a fake.
"""

from dataclasses import dataclass
import json
import logging
import os
import socket
import time
import uuid

import atlas_store

logger = logging.getLogger(__name__)

DEFAULT_TTL_SECONDS = 300

PRECONDITION_CODES = atlas_store.PRECONDITION_ERROR_CODES
MISSING_CODES = atlas_store.MISSING_ERROR_CODES


class AtlasLocked(Exception):
    """Another holder has an unexpired lease on this atlas."""

    def __init__(self, atlas_name: str, holder):
        self.atlas_name = atlas_name
        self.holder = holder or {}
        super().__init__(
            f"atlas '{atlas_name}' is locked by {self.holder.get('owner', 'unknown')} "
            f"({self.holder.get('purpose') or 'no purpose given'})")


class LeaseLost(Exception):
    """The lease was taken over or removed; the holder must not write back."""


@dataclass
class Lease:
    bucket: str
    key: str
    atlas_name: str
    owner: str
    purpose: str
    ttl: float
    acquired_at: float
    expires_at: float
    etag: str


def lock_key(atlas_name: str) -> str:
    """Beside staging/, never inside it — a hydrate must not see the lock as data."""
    return f"{atlas_store.atlas_prefix(atlas_name)}/lock"


def default_owner() -> str:
    """Host, pid and a random suffix — enough to tell holders apart in a log."""
    return f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:8]}"


_error_code = atlas_store.s3_error_code


def _document(owner, purpose, acquired_at, now, ttl) -> bytes:
    return json.dumps({
        'owner': owner,
        'purpose': purpose,
        'acquired_at': acquired_at,
        'renewed_at': now,
        'expires_at': now + ttl,
    }).encode('utf-8')


def _put(client, bucket, key, body, **condition):
    return client.put_object(Bucket=bucket, Key=key, Body=body,
                             ContentType='application/json', **condition)


def _read_holder(client, bucket, key):
    """``(holder document, etag)``, or ``(None, None)`` when there is no lock."""
    try:
        current = client.get_object(Bucket=bucket, Key=key)
    except Exception as exc:
        if _error_code(exc) in MISSING_CODES:
            return None, None
        raise
    raw = current['Body'].read()
    try:
        holder = json.loads(raw)
        if not isinstance(holder, dict):
            raise ValueError('lock document is not an object')
    except ValueError:
        holder = {'owner': 'unreadable lock document', 'raw': raw[:200].decode('utf-8', 'replace')}
    return holder, current['ETag']


def _expired(holder: dict, now: float) -> bool:
    """A document with no usable expiry reads as expired, so it cannot wedge an atlas."""
    try:
        return now >= float(holder['expires_at'])
    except (KeyError, TypeError, ValueError):
        return True


def _try_acquire(client, bucket, atlas_name, owner, purpose, ttl, clock):
    """One attempt. Returns a Lease, a holder dict, or None if the lock vanished mid-attempt."""
    key = lock_key(atlas_name)
    now = clock()

    def lease_from(response):
        return Lease(bucket=bucket, key=key, atlas_name=atlas_name, owner=owner,
                     purpose=purpose, ttl=ttl, acquired_at=now,
                     expires_at=now + ttl, etag=response['ETag'])

    body = _document(owner, purpose, now, now, ttl)
    try:
        return lease_from(_put(client, bucket, key, body, IfNoneMatch='*'))
    except Exception as exc:
        if _error_code(exc) not in PRECONDITION_CODES:
            raise

    holder, etag = _read_holder(client, bucket, key)
    if holder is None:
        return None
    if not _expired(holder, now):
        return holder

    try:
        response = _put(client, bucket, key, body, IfMatch=etag)
    except Exception as exc:
        code = _error_code(exc)
        if code in MISSING_CODES:
            return None
        if code in PRECONDITION_CODES:
            return holder
        raise
    logger.warning(f"atlas_lock: {owner} took over expired lease on '{atlas_name}' "
                   f"from {holder.get('owner')} ({holder.get('purpose')})")
    return lease_from(response)


def acquire(client, bucket: str, atlas_name: str, purpose: str = '',
            ttl: float = DEFAULT_TTL_SECONDS, wait: float = 0.0, poll: float = 1.0,
            owner: str = None, clock=time.time, sleep=time.sleep) -> Lease:
    """Take the atlas's lease, waiting up to ``wait`` seconds for a holder to finish.

    Raises ``AtlasLocked`` when the lease is still held at the deadline.
    ``clock`` and ``sleep`` are injectable so tests do not wait in real time.
    """
    owner = owner or default_owner()
    deadline = clock() + wait
    vanished = 0
    while True:
        result = _try_acquire(client, bucket, atlas_name, owner, purpose, ttl, clock)
        if isinstance(result, Lease):
            return result
        if result is None and vanished < 3:
            # Released between our PUT and GET. Free now — try again at once.
            vanished += 1
            continue
        if clock() >= deadline:
            raise AtlasLocked(atlas_name, result)
        sleep(poll)


def renew(client, lease: Lease, clock=time.time) -> Lease:
    """Extend the lease. Raises ``LeaseLost`` if it is no longer ours.

    Succeeds even past expiry when nobody took the lease over — the
    conditional PUT is the arbiter, not the clock.
    """
    now = clock()
    body = _document(lease.owner, lease.purpose, lease.acquired_at, now, lease.ttl)
    try:
        response = _put(client, lease.bucket, lease.key, body, IfMatch=lease.etag)
    except Exception as exc:
        if _error_code(exc) in PRECONDITION_CODES | MISSING_CODES:
            raise LeaseLost(f"lease on '{lease.atlas_name}' held by {lease.owner} "
                            f"was taken over or removed") from exc
        raise
    lease.etag = response['ETag']
    lease.expires_at = now + lease.ttl
    return lease


def release(client, lease: Lease, clock=time.time) -> bool:
    """Give the lease up. Returns True if this call removed the lock."""
    holder, etag = _read_holder(client, lease.bucket, lease.key)
    if holder is None:
        logger.warning(f"atlas_lock: lease on '{lease.atlas_name}' was already gone at release")
        return False
    if etag != lease.etag:
        logger.warning(f"atlas_lock: lease on '{lease.atlas_name}' now belongs to "
                       f"{holder.get('owner')}; not releasing")
        return False
    if clock() >= lease.expires_at:
        # Expired: another process may be taking it over with this very ETag.
        # Leave it to them rather than delete a lock that may be theirs a
        # moment from now.
        logger.warning(f"atlas_lock: lease on '{lease.atlas_name}' expired before release; "
                       f"leaving it to expire")
        return False
    client.delete_object(Bucket=lease.bucket, Key=lease.key)
    return True


def describe(client, bucket: str, atlas_name: str):
    """The current holder's document, or None — for status pages and break-glass."""
    holder, _etag = _read_holder(client, bucket, lock_key(atlas_name))
    return holder
