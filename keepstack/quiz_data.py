"""Deterministic data for the search / export scenario.

``python -m keepstack seed-quiz`` adds a "Harbor Campaign" collection whose
assets exercise every rule of the combined filter: required and excluded tags,
collection membership, non-active status, identical created_at values (paging
ties) and CSV-hostile text (Chinese, commas, quotes, newlines).

The same ``add_asset`` helper is used by the automated tests, so the demo data
and the tested data are built the same way.
"""
from __future__ import annotations

import hashlib
import io
import uuid as uuidlib
from typing import Iterable, Optional

from PIL import Image

from . import ingest
from .audit import now_iso
from .db import get_conn, init_db

COLLECTION_NAME = "Harbor Campaign"
TIED_CREATED_AT = "2026-02-01T00:00:00+00:00"
TIED_COUNT = 12


def _image_for(key: str) -> bytes:
    """A small JPEG whose pixels derive from ``key`` so every asset has its own
    sha256 (ingest dedups identical blobs) and reruns are reproducible."""
    raw = b""
    h = key.encode("utf-8")
    while len(raw) < 16 * 16 * 3:
        h = hashlib.sha256(h).digest()
        raw += h
    img = Image.frombytes("RGB", (16, 16), raw[: 16 * 16 * 3]).resize((64, 64), Image.NEAREST)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=95)
    return buf.getvalue()


def ensure_collection(name: str, description: str = "") -> int:
    conn = get_conn()
    row = conn.execute("SELECT id FROM collections WHERE name = ?", (name,)).fetchone()
    if row:
        return row["id"]
    cur = conn.execute(
        "INSERT INTO collections (uuid, name, description, kind, created_at) VALUES (?,?,?, 'folder', ?)",
        (str(uuidlib.uuid4()), name, description, now_iso()))
    conn.commit()
    return cur.lastrowid


def add_asset(
    *,
    title: str,
    created_at: str,
    description: str = "",
    tags: Iterable[str] = (),
    collection_id: Optional[int] = None,
    status: str = "active",
    filename: Optional[str] = None,
    salt: str = "",
) -> dict:
    """Ingest a synthetic image and pin its descriptive fields and created_at."""
    key = f"{salt}|{title}|{created_at}"
    if filename is None:
        # Camera-style name: filename is an FTS field, so a title-derived name
        # would keep matching the title's words after the title is edited.
        filename = f"IMG_{hashlib.sha256(key.encode('utf-8')).hexdigest()[:8]}.jpg"
    asset = ingest.ingest_stream(io.BytesIO(_image_for(key)), filename,
                                 run_ai=False, title=title)
    conn = get_conn()
    conn.execute("UPDATE assets SET description = ?, created_at = ?, status = ? WHERE id = ?",
                 (description, created_at, status, asset["id"]))
    ingest._set_tags(conn, asset["id"], [(t, 1.0) for t in tags], "manual")
    if collection_id is not None:
        conn.execute("INSERT OR IGNORE INTO collection_assets (collection_id, asset_id, added_at) VALUES (?,?,?)",
                     (collection_id, asset["id"], now_iso()))
    conn.commit()
    ingest.reindex_fts(asset["id"])
    return dict(conn.execute("SELECT * FROM assets WHERE id = ?", (asset["id"],)).fetchone())


def build_scenario(collection_id: int, other_collection_id: int, salt: str = "") -> list[str]:
    """Populate the scenario; return the titles that should match
    q=harbor, tags=[approved, campaign], exclude_tags=[internal] within
    ``collection_id``, in "oldest" order (ties broken by insertion order)."""
    ok = ["approved", "campaign"]
    cid, kw = collection_id, dict(salt=salt)
    add_asset(title="Harbor sunrise hero", created_at="2026-01-05T08:00:00+00:00",
              description="Main visual for the spring campaign", tags=ok, collection_id=cid, **kw)
    add_asset(title="Harbor ferry 港口渡轮", created_at="2026-03-01T10:30:00+00:00",
              description='Ferry at pier 3, "golden hour", wide\nsecond line, 中文说明',
              tags=[*ok, "Ferry"], collection_id=cid, **kw)
    for i in range(1, TIED_COUNT + 1):
        add_asset(title=f"Harbor batch shot {i:02d}", created_at=TIED_CREATED_AT,
                  description="Bulk import with an identical timestamp", tags=ok, collection_id=cid, **kw)

    # Each of these must be filtered out by exactly one rule.
    add_asset(title="Harbor night panorama", created_at="2026-01-10T20:00:00+00:00",
              description="Internal review copy", tags=[*ok, "internal"], collection_id=cid, **kw)
    add_asset(title="Harbor crane detail", created_at="2026-01-12T09:00:00+00:00",
              description="Waiting for sign-off", tags=["approved"], collection_id=cid, **kw)
    add_asset(title="Harbor lighthouse", created_at="2026-01-15T09:00:00+00:00",
              description="Approved, but filed in another collection", tags=ok,
              collection_id=other_collection_id, **kw)
    add_asset(title="Seaside market", created_at="2026-01-18T09:00:00+00:00",
              description="No keyword match", tags=ok, collection_id=cid, **kw)
    add_asset(title="Harbor retired poster", created_at="2026-01-20T09:00:00+00:00",
              description="Soft-deleted", tags=ok, collection_id=cid, status="deleted", **kw)
    return (["Harbor sunrise hero"]
            + [f"Harbor batch shot {i:02d}" for i in range(1, TIED_COUNT + 1)]
            + ["Harbor ferry 港口渡轮"])


def run_seed_quiz() -> None:
    init_db()
    conn = get_conn()
    if conn.execute("SELECT 1 FROM collections WHERE name = ?", (COLLECTION_NAME,)).fetchone():
        print(f"'{COLLECTION_NAME}' already exists; nothing to do.")
        return
    cid = ensure_collection(COLLECTION_NAME, "Search/export scenario data (python -m keepstack seed-quiz)")
    other = ensure_collection("Harbor Archive", "Harbor assets outside the campaign collection")
    expected = build_scenario(cid, other)
    print(f"Added quiz scenario: collection '{COLLECTION_NAME}' with {TIED_COUNT + 6} assets "
          f"(+1 in 'Harbor Archive'). Searching 'harbor' there with tags approved+campaign "
          f"and excluding internal should return {len(expected)} assets.")
