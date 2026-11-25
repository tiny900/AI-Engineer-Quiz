"""Combined filters, stable paging, CSV export and edit-then-query behaviour.

Every test builds its own copy of the quiz scenario (keepstack/quiz_data.py)
inside a fresh collection and scopes its queries to that collection, so tests
are independent of each other and of the other test modules sharing the DB.
"""
import csv
import io
import os
import tempfile
import uuid

os.environ.setdefault("KEEPSTACK_DATA_DIR", tempfile.mkdtemp(prefix="keepstack-test-"))
os.environ.setdefault("KEEPSTACK_AI_ENABLED", "false")
os.environ.setdefault("KEEPSTACK_OCR_ENABLED", "false")

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from keepstack import quiz_data  # noqa: E402
from keepstack.app import app  # noqa: E402
from keepstack.auth import make_token  # noqa: E402
from keepstack.db import get_conn  # noqa: E402

SCENARIO = {"q": "harbor", "tags": ["approved", "campaign"], "exclude_tags": ["internal"]}


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:  # runs the startup hook (schema + admin user)
        admin = get_conn().execute("SELECT id FROM users WHERE role = 'admin' LIMIT 1").fetchone()
        c.headers["Authorization"] = "Bearer " + make_token(admin["id"], "admin")
        yield c


@pytest.fixture
def scenario():
    """A fresh campaign collection + 'other' collection; returns (cid, expected oldest-first titles)."""
    salt = uuid.uuid4().hex
    cid = quiz_data.ensure_collection(f"Campaign {salt}")
    other = quiz_data.ensure_collection(f"Other {salt}")
    return cid, quiz_data.build_scenario(cid, other, salt=salt)


def _list(client, **params):
    r = client.get("/api/assets", params=params)
    assert r.status_code == 200, r.text
    return r.json()


def _all_pages(client, limit, **params):
    """Walk next-page links until exhausted; return the concatenated items."""
    items, offset = [], 0
    first = _list(client, limit=limit, offset=0, **params)
    total = first["total"]
    while True:
        page = first if offset == 0 else _list(client, limit=limit, offset=offset, **params)
        assert page["total"] == total
        items += page["items"]
        offset += limit
        if offset >= total:
            return items, total


def _export(client, **params):
    r = client.get("/api/assets/export.csv", params=params)
    assert r.status_code == 200, r.text
    assert r.headers["content-type"].startswith("text/csv")
    assert "attachment" in r.headers["content-disposition"]
    text = r.content.decode("utf-8-sig")  # BOM for Excel
    return list(csv.DictReader(io.StringIO(text, newline="")))


# ---------- combined filters ----------

def test_combined_filter_matches_business_scenario(client, scenario):
    cid, expected = scenario
    data = _list(client, collection_id=cid, sort="oldest", limit=200, **SCENARIO)
    assert [i["title"] for i in data["items"]] == expected
    assert data["total"] == len(expected)
    assert data["filters"]["tags"] == ["approved", "campaign"]
    assert data["filters"]["exclude_tags"] == ["internal"]


def test_each_condition_narrows_independently(client, scenario):
    cid, expected = scenario
    titles = lambda **p: {i["title"] for i in _list(client, collection_id=cid, limit=200, **p)["items"]}  # noqa: E731
    # Without the exclusion the internal asset comes back.
    assert "Harbor night panorama" in titles(q="harbor", tags=["approved", "campaign"])
    # Requiring only "approved" lets the not-yet-campaign asset in.
    assert "Harbor crane detail" in titles(q="harbor", tags=["approved"], exclude_tags=["internal"])
    # Without the keyword the non-harbor asset matches.
    assert "Seaside market" in titles(tags=["approved", "campaign"], exclude_tags=["internal"])
    # Never: other collection, soft-deleted.
    everything = titles()
    assert "Harbor lighthouse" not in everything
    assert "Harbor retired poster" not in everything


def test_tags_match_case_insensitively_and_duplicates_are_ignored(client, scenario):
    cid, expected = scenario
    base = _list(client, collection_id=cid, limit=200, **SCENARIO)
    noisy = _list(client, collection_id=cid, limit=200, q="  HARBOR ",
                  tags=["Approved", "approved", " campaign ", "CAMPAIGN"],
                  exclude_tags=["internal", "INTERNAL"])
    assert [i["uuid"] for i in noisy["items"]] == [i["uuid"] for i in base["items"]]
    ferry = _list(client, collection_id=cid, tags=["FERRY"])
    assert [i["title"] for i in ferry["items"]] == ["Harbor ferry 港口渡轮"]


def test_tags_match_whole_names_only(client, scenario):
    cid, _ = scenario
    assert _list(client, collection_id=cid, tags=["approv"])["total"] == 0
    assert _list(client, collection_id=cid, tags=["approved"], exclude_tags=["intern"])["total"] > 0


def test_tag_both_included_and_excluded_returns_nothing(client, scenario):
    cid, _ = scenario
    data = _list(client, collection_id=cid, tags=["approved"], exclude_tags=["APPROVED"])
    assert data["total"] == 0 and data["items"] == []
    assert _export(client, collection_id=cid, tags=["approved"], exclude_tags=["approved"]) == []


def test_media_type_filter_applies_to_list_and_export(client, scenario):
    cid, expected = scenario
    assert _list(client, collection_id=cid, media_type="video", **SCENARIO)["total"] == 0
    assert _export(client, collection_id=cid, media_type="video", **SCENARIO) == []
    assert len(_export(client, collection_id=cid, media_type="image", **SCENARIO)) == len(expected)


# ---------- sorting & paging ----------

def test_sort_is_honoured_with_a_keyword(client, scenario):
    cid, expected = scenario
    oldest = [i["title"] for i in _list(client, collection_id=cid, sort="oldest", limit=200, **SCENARIO)["items"]]
    newest = [i["title"] for i in _list(client, collection_id=cid, sort="newest", limit=200, **SCENARIO)["items"]]
    assert oldest == expected
    assert newest == list(reversed(expected))


@pytest.mark.parametrize("sort", ["oldest", "newest"])
@pytest.mark.parametrize("limit", [1, 4, 5, 13])
def test_paging_over_identical_created_at_has_no_gaps_or_duplicates(client, scenario, sort, limit):
    cid, expected = scenario
    paged, total = _all_pages(client, limit, collection_id=cid, sort=sort, **SCENARIO)
    full = _list(client, collection_id=cid, sort=sort, limit=200, **SCENARIO)["items"]
    assert total == len(expected) == len(paged)
    assert [i["uuid"] for i in paged] == [i["uuid"] for i in full]
    assert len({i["uuid"] for i in paged}) == len(paged)
    # Repeating the walk yields the identical order (stable with unchanged data).
    again, _ = _all_pages(client, limit, collection_id=cid, sort=sort, **SCENARIO)
    assert [i["uuid"] for i in again] == [i["uuid"] for i in paged]


def test_offset_past_the_end_is_an_empty_page_not_an_error(client, scenario):
    cid, expected = scenario
    data = _list(client, collection_id=cid, offset=1000, **SCENARIO)
    assert data["items"] == [] and data["total"] == len(expected)


@pytest.mark.parametrize("params", [
    {"limit": 0}, {"limit": -5}, {"limit": 201}, {"offset": -1},
    {"limit": "abc"}, {"offset": "1.5"}, {"sort": "bogus"},
])
def test_invalid_paging_and_sort_params_are_rejected(client, params):
    assert client.get("/api/assets", params=params).status_code == 422


@pytest.mark.parametrize("q", ['"', "'", "!!!", "*", "harbor\"", "harbor OR", "NOT", "(", "%_", "港口", "a\nb"])
def test_special_characters_never_break_search(client, scenario, q):
    cid, _ = scenario
    r = client.get("/api/assets", params={"q": q, "collection_id": cid})
    assert r.status_code == 200, r.text
    assert client.get("/api/assets/export.csv", params={"q": q, "collection_id": cid}).status_code == 200


def test_punctuation_only_keyword_behaves_like_blank(client, scenario):
    cid, _ = scenario
    assert (_list(client, collection_id=cid, q="!!! ")["total"]
            == _list(client, collection_id=cid, q="   ")["total"]
            == _list(client, collection_id=cid)["total"])


def test_chinese_keyword_uses_existing_tokenizer(client, scenario):
    cid, _ = scenario
    assert [i["title"] for i in _list(client, collection_id=cid, q="港口渡轮")["items"]] == ["Harbor ferry 港口渡轮"]


# ---------- CSV export ----------

@pytest.mark.parametrize("sort", ["oldest", "newest"])
def test_export_equals_all_list_pages_in_order(client, scenario, sort):
    cid, expected = scenario
    paged, _ = _all_pages(client, 5, collection_id=cid, sort=sort, **SCENARIO)
    rows = _export(client, collection_id=cid, sort=sort, **SCENARIO)
    assert [r["uuid"] for r in rows] == [i["uuid"] for i in paged]
    assert len(rows) == len(expected)


def test_export_columns_and_csv_hostile_text_round_trip(client, scenario):
    cid, _ = scenario
    r = client.get("/api/assets/export.csv", params={"collection_id": cid, **SCENARIO})
    header = r.content.decode("utf-8-sig").split("\r\n", 1)[0]
    assert header == "uuid,title,description,filename,media_type,tags,created_at"
    ferry = next(row for row in _export(client, collection_id=cid, **SCENARIO) if "ferry" in row["title"])
    assert ferry["title"] == "Harbor ferry 港口渡轮"
    assert ferry["description"] == 'Ferry at pier 3, "golden hour", wide\nsecond line, 中文说明'
    assert ferry["tags"] == "approved|campaign|ferry"
    assert ferry["media_type"] == "image"
    assert ferry["filename"].endswith(".jpg")
    assert ferry["created_at"] == "2026-03-01T10:30:00+00:00"


def test_export_with_zero_matches_still_has_header(client, scenario):
    cid, _ = scenario
    r = client.get("/api/assets/export.csv", params={"collection_id": cid, "q": "nosuchwordanywhere"})
    assert r.status_code == 200
    assert r.content.decode("utf-8-sig") == "uuid,title,description,filename,media_type,tags,created_at\r\n"


def test_export_requires_login():
    with TestClient(app) as anon:
        assert anon.get("/api/assets/export.csv").status_code == 401
        assert anon.get("/api/assets/export.csv", headers={"Authorization": "Bearer junk"}).status_code == 401


# ---------- edits are reflected ----------

def test_edits_are_reflected_in_next_query_and_export(client, scenario):
    cid, expected = scenario
    q = dict(collection_id=cid, limit=200, **SCENARIO)
    hero = next(i for i in _list(client, **q)["items"] if i["title"] == "Harbor sunrise hero")
    uid = hero["uuid"]
    in_list = lambda: uid in {i["uuid"] for i in _list(client, **q)["items"]}  # noqa: E731
    in_export = lambda: uid in {r["uuid"] for r in _export(client, **q)}  # noqa: E731

    # Title loses the keyword -> gone; description gains it -> back.
    assert client.patch(f"/api/assets/{uid}", json={"title": "Sunrise hero"}).status_code == 200
    assert not in_list() and not in_export()
    assert client.patch(f"/api/assets/{uid}", json={"description": "Shot from the harbor wall"}).status_code == 200
    assert in_list() and in_export()
    row = next(r for r in _export(client, **q) if r["uuid"] == uid)
    assert row["title"] == "Sunrise hero" and row["description"] == "Shot from the harbor wall"

    # Adding an excluded tag removes it; removing that tag brings it back.
    assert client.post(f"/api/assets/{uid}/tags", json={"name": "Internal"}).status_code == 200
    assert not in_list() and not in_export()
    assert client.delete(f"/api/assets/{uid}/tags/internal").status_code == 200
    assert in_list() and in_export()

    # Removing a required tag removes it; the export's tags column follows edits.
    assert client.delete(f"/api/assets/{uid}/tags/campaign").status_code == 200
    assert not in_list() and not in_export()
    client.post(f"/api/assets/{uid}/tags", json={"name": "campaign"})
    client.post(f"/api/assets/{uid}/tags", json={"name": "hero shot"})
    row = next(r for r in _export(client, **q) if r["uuid"] == uid)
    assert row["tags"] == "approved|campaign|hero shot"

    # Soft delete -> no longer active -> gone everywhere.
    assert client.delete(f"/api/assets/{uid}").status_code == 200
    assert not in_list() and not in_export()
