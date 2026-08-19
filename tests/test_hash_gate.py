"""
Defect 4 -- the hash gate is content-blind, and legacy DBs lie.

The gate hashes ``label + file_path``. Neither changes when someone edits the
body of a function, so a node is never marked dirty and never re-enriched.
graphify already publishes per-file content hashes in
``graphify-out/manifest.json``; those must drive the signature instead.

Separately, older databases are full of generated placeholder summaries
("Layer 3: Foo located at bar.ts") that look enriched to ``check_node``, so
those nodes are permanently skipped. A schema bump to version 2 adds an
``intent`` column and purges those rows -- without touching genuinely enriched
ones.
"""

import json
import sqlite3

import pytest

from codechakra.graph_loader import GraphLoader
from codechakra.hash_gate import HashGate


# ---------------------------------------------------------------------------
# Content-driven signature
# ---------------------------------------------------------------------------

def test_loader_loads_the_graphify_manifest(loader, mini_repo):
    assert hasattr(loader, "file_hashes"), (
        "GraphLoader must load graphify-out/manifest.json into self.file_hashes"
    )
    manifest = mini_repo.read_manifest()
    for rel, entry in manifest.items():
        assert loader.file_hashes.get(rel) == entry["semantic_hash"], (
            f"file_hashes[{rel!r}] should be the semantic_hash"
        )


def test_signature_uses_file_hash_node_id_and_source_location(loader, mini_repo):
    key = "svc_pension"
    nid = mini_repo.nid(key)
    src = mini_repo.source_file(key)
    loc = mini_repo.node_specs[key][2]

    attrs = {
        "id": nid,
        "label": mini_repo.label(key),
        "file": src,
        "source_location": loc,
    }
    expected = f"{mini_repo.read_manifest()[src]['semantic_hash']}|{nid}|{loc}"
    assert loader.node_signature(attrs) == expected


def test_signature_changes_when_source_location_moves(loader, mini_repo):
    key = "svc_pension"
    base = {
        "id": mini_repo.nid(key),
        "label": mini_repo.label(key),
        "file": mini_repo.source_file(key),
        "source_location": mini_repo.node_specs[key][2],
    }
    moved = dict(base, source_location="L999")
    assert loader.node_signature(base) != loader.node_signature(moved)


def test_signature_falls_back_to_file_bytes_when_not_in_manifest(loader, mini_repo):
    """
    A real file graphify did not record still has to produce a content-derived
    signature, not the legacy label+path one.
    """
    extra = mini_repo.root / "shared" / "utils" / "unlisted.ts"
    extra.write_text("export const unlisted = 1;\n", encoding="utf-8")

    attrs = {
        "id": "shared_utils_unlisted_unlisted",
        "label": "unlisted",
        "file": "shared/utils/unlisted.ts",
        "source_location": "L1",
    }
    first = loader.node_signature(attrs)
    assert first not in ("", None)
    assert first != "unlisted" + "shared/utils/unlisted.ts"

    extra.write_text("export const unlisted = 2;\n", encoding="utf-8")
    reloaded = GraphLoader(str(mini_repo.root))
    assert reloaded.node_signature(attrs) != first, (
        "sha256 fallback did not react to a content change"
    )


def test_signature_falls_back_to_legacy_when_file_is_missing(loader):
    attrs = {
        "id": "ghost_node",
        "label": "Ghost",
        "file": "does/not/exist.ts",
        "source_location": "L1",
    }
    assert loader.node_signature(attrs)  # must not raise, must not be empty


def test_node_is_clean_when_manifest_is_unchanged(mini_repo, no_network):
    l1 = GraphLoader(str(mini_repo.root))
    l1.load_or_extract(enrich_llm=True)

    nid = mini_repo.nid("svc_pension")
    attrs = dict(l1.graph.nodes[nid])

    l2 = GraphLoader(str(mini_repo.root))
    is_dirty, cached = l2.hash_gate.check_node(nid, l2.node_signature(attrs))

    assert not is_dirty, "unchanged file should leave the node clean"
    assert cached is not None


def test_node_goes_dirty_when_semantic_hash_changes(mini_repo, no_network):
    l1 = GraphLoader(str(mini_repo.root))
    l1.load_or_extract(enrich_llm=True)

    key = "svc_pension"
    nid = mini_repo.nid(key)
    attrs = dict(l1.graph.nodes[nid])

    # graphify saw a real edit to the file's contents.
    mini_repo.bump_semantic_hash(key)

    l2 = GraphLoader(str(mini_repo.root))
    is_dirty, _ = l2.hash_gate.check_node(nid, l2.node_signature(attrs))

    assert is_dirty, (
        "a changed semantic_hash in graphify-out/manifest.json must mark the "
        "node dirty -- the gate is still hashing label+path"
    )


def test_untouched_sibling_stays_clean_after_one_file_changes(mini_repo, no_network):
    l1 = GraphLoader(str(mini_repo.root))
    l1.load_or_extract(enrich_llm=True)

    other = mini_repo.nid("api_controller")
    attrs = dict(l1.graph.nodes[other])

    mini_repo.bump_semantic_hash("svc_pension")

    l2 = GraphLoader(str(mini_repo.root))
    is_dirty, _ = l2.hash_gate.check_node(other, l2.node_signature(attrs))
    assert not is_dirty, "changing one file must not dirty an unrelated node"


# ---------------------------------------------------------------------------
# intent column
# ---------------------------------------------------------------------------

def test_schema_version_is_two():
    assert HashGate.SCHEMA_VERSION == 2


def test_intent_round_trips_through_update_and_check(tmp_path):
    gate = HashGate(str(tmp_path / ".codechakra" / "codechakra.db"))
    gate.update_node(
        node_id="n1",
        file_path="backend/src/a.ts",
        content="sig-v1",
        layer="Layer 3: Domain Service",
        summary="Layer 3: A - does a thing",
        fields_json=json.dumps(["caseId"]),
        intent="Does a thing with a case.",
    )

    is_dirty, cached = gate.check_node("n1", "sig-v1")
    assert not is_dirty
    assert cached["intent"] == "Does a thing with a case."
    assert cached["summary"] == "Layer 3: A - does a thing"
    assert json.loads(cached["fields_json"]) == ["caseId"]


def test_intent_defaults_to_empty_string(tmp_path):
    gate = HashGate(str(tmp_path / ".codechakra" / "codechakra.db"))
    gate.update_node(
        node_id="n2",
        file_path="b.ts",
        content="sig",
        layer="Layer 3: Domain Service",
        summary="s",
    )
    _, cached = gate.check_node("n2", "sig")
    assert cached["intent"] in ("", None)


def test_fresh_db_is_stamped_at_version_two(tmp_path):
    db = tmp_path / ".codechakra" / "codechakra.db"
    HashGate(str(db))
    assert _user_version(db) == 2


# ---------------------------------------------------------------------------
# v1 -> v2 migration
# ---------------------------------------------------------------------------

PLACEHOLDER_ID = "legacy_placeholder"
PLACEHOLDER_WITH_FIELDS_ID = "legacy_placeholder_but_has_fields"
ENRICHED_ID = "legacy_enriched"


def _user_version(db_path) -> int:
    conn = sqlite3.connect(str(db_path))
    try:
        return conn.execute("PRAGMA user_version").fetchone()[0]
    finally:
        conn.close()


def _columns(db_path):
    conn = sqlite3.connect(str(db_path))
    try:
        return {r[1] for r in conn.execute("PRAGMA table_info(node_cache)")}
    finally:
        conn.close()


def _node_ids(db_path):
    conn = sqlite3.connect(str(db_path))
    try:
        return {r[0] for r in conn.execute("SELECT node_id FROM node_cache")}
    finally:
        conn.close()


@pytest.fixture
def legacy_db(tmp_path):
    """A v1-shaped database: no intent column, user_version 0, mixed rows."""
    db = tmp_path / ".codechakra" / "codechakra.db"
    db.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(str(db))
    conn.execute(
        """
        CREATE TABLE node_cache (
            node_id TEXT PRIMARY KEY,
            file_path TEXT,
            content_hash TEXT,
            layer TEXT,
            summary TEXT,
            fields_json TEXT,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    conn.execute("CREATE INDEX idx_file_path ON node_cache(file_path)")
    conn.executemany(
        "INSERT INTO node_cache (node_id, file_path, content_hash, layer, summary, fields_json)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        [
            (
                PLACEHOLDER_ID,
                "backend/src/foo/bar.ts",
                "abc123",
                "Layer 3: Domain Service",
                "Layer 3: Foo located at bar.ts",
                "[]",
            ),
            (
                PLACEHOLDER_WITH_FIELDS_ID,
                "backend/src/foo/baz.ts",
                "def456",
                "Layer 3: Domain Service",
                "Layer 3: Baz located at baz.ts",
                '["caseId", "remarks"]',
            ),
            (
                ENRICHED_ID,
                "backend/src/cases/cases.controller.ts",
                "ghi789",
                "Layer 2: API Gateway",
                "Layer 2: CasesController - Accepts case submissions and delegates to the workflow service.",
                '["caseId", "payload"]',
            ),
        ],
    )
    conn.execute("PRAGMA user_version = 0")
    conn.commit()
    conn.close()
    return db


def test_migration_adds_the_intent_column(legacy_db):
    assert "intent" not in _columns(legacy_db)
    HashGate(str(legacy_db))
    assert "intent" in _columns(legacy_db)


def test_migration_stamps_user_version_two(legacy_db):
    assert _user_version(legacy_db) == 0
    HashGate(str(legacy_db))
    assert _user_version(legacy_db) == 2


def test_migration_purges_placeholder_rows(legacy_db):
    HashGate(str(legacy_db))
    assert PLACEHOLDER_ID not in _node_ids(legacy_db), (
        "placeholder summary row survived the v2 migration"
    )


def test_migration_keeps_genuinely_enriched_rows(legacy_db):
    HashGate(str(legacy_db))
    ids = _node_ids(legacy_db)
    assert ENRICHED_ID in ids, "a genuinely enriched row was purged"
    assert PLACEHOLDER_WITH_FIELDS_ID in ids, (
        "a row with a placeholder-looking summary but real fields was purged"
    )


def test_purge_placeholders_is_callable_and_reports_a_count(legacy_db):
    gate = HashGate(str(legacy_db))          # migration already purged
    assert gate.purge_placeholders() == 0    # nothing left to purge

    gate.update_node(
        node_id="fresh_placeholder",
        file_path="x.ts",
        content="sig",
        layer="Layer 3: Domain Service",
        summary="Layer 3: X located at x.ts",
        fields_json="[]",
        intent="",
    )
    assert gate.purge_placeholders() == 1
    assert "fresh_placeholder" not in _node_ids(legacy_db)


def test_migration_is_idempotent(legacy_db):
    HashGate(str(legacy_db))
    before = _node_ids(legacy_db)
    HashGate(str(legacy_db))
    assert _node_ids(legacy_db) == before
    assert _user_version(legacy_db) == 2


def test_purged_node_is_reported_dirty(legacy_db):
    gate = HashGate(str(legacy_db))
    is_dirty, cached = gate.check_node(PLACEHOLDER_ID, "abc123")
    assert is_dirty, "a purged placeholder node must come back as dirty"
    assert cached is None
