from __future__ import annotations

from app.worker.normalizer import _canonical, compute_diff, content_hash, normalize


class TestNormalize:
    def test_drops_excluded_fields(self) -> None:
        payload = {
            "notice_id": "X",
            "name": "Smith",
            "cycle_id": "abc",
            "thumbnail_url": "https://example.com/img.jpg",
            "_links": {"self": {"href": "..."}},
        }
        result = normalize(payload)
        assert "cycle_id" not in result
        assert "thumbnail_url" not in result
        assert "_links" not in result
        assert result["notice_id"] == "X"
        assert result["name"] == "Smith"

    def test_keeps_all_non_excluded_fields(self) -> None:
        payload = {
            "notice_id": "2021/1",
            "forename": "Jane",
            "name": "Doe",
            "nationalities": ["TR"],
            "arrest_warrant_countries": ["TR", "DE"],
            "sex_id": "F",
            "date_of_birth": "1990/01/01",
            "cycle_id": "skip-me",
        }
        result = normalize(payload)
        assert result["forename"] == "Jane"
        assert result["nationalities"] == ["TR"]
        assert "cycle_id" not in result


class TestContentHash:
    def test_same_payload_same_hash(self) -> None:
        p = {"notice_id": "X", "name": "Smith"}
        assert content_hash(p) == content_hash(p)

    def test_different_payload_different_hash(self) -> None:
        assert content_hash({"name": "Smith"}) != content_hash({"name": "Jones"})

    def test_key_order_independent(self) -> None:
        a = {"z": 1, "a": 2}
        b = {"a": 2, "z": 1}
        assert content_hash(a) == content_hash(b)

    def test_thumbnail_url_excluded_so_same_hash(self) -> None:
        """A changed photo URL must NOT change the content hash (policy: photo
        replacements are not surfaced as 'updated' alarms)."""
        p1 = normalize({"notice_id": "X", "name": "A", "thumbnail_url": "old.jpg"})
        p2 = normalize({"notice_id": "X", "name": "A", "thumbnail_url": "new.jpg"})
        assert content_hash(p1) == content_hash(p2)

    def test_returns_64_char_hex(self) -> None:
        h = content_hash({"x": 1})
        assert len(h) == 64
        assert all(c in "0123456789abcdef" for c in h)


class TestComputeDiff:
    def test_scalar_change(self) -> None:
        diff = compute_diff({"name": "Smith"}, {"name": "Jones"})
        assert diff == {"name": {"old": "Smith", "new": "Jones"}}

    def test_no_change(self) -> None:
        assert compute_diff({"name": "X"}, {"name": "X"}) == {}

    def test_list_order_independent(self) -> None:
        """["TR","US"] and ["US","TR"] must be considered equal."""
        assert compute_diff(
            {"nationalities": ["TR", "US"]},
            {"nationalities": ["US", "TR"]},
        ) == {}

    def test_list_content_change_detected(self) -> None:
        diff = compute_diff(
            {"nationalities": ["TR"]},
            {"nationalities": ["TR", "US"]},
        )
        assert "nationalities" in diff
        assert diff["nationalities"]["old"] == ["TR"]
        assert diff["nationalities"]["new"] == ["TR", "US"]

    def test_nested_list_of_dicts_order_independent(self) -> None:
        """arrest_warrant_countries may be a list of dicts; order should not matter."""
        a = [{"country": "TR", "charge": "fraud"}, {"country": "DE", "charge": "fraud"}]
        b = [{"country": "DE", "charge": "fraud"}, {"country": "TR", "charge": "fraud"}]
        assert compute_diff({"awc": a}, {"awc": b}) == {}

    def test_nested_list_change_detected(self) -> None:
        a = [{"country": "TR"}]
        b = [{"country": "DE"}]
        diff = compute_diff({"awc": a}, {"awc": b})
        assert "awc" in diff

    def test_field_added(self) -> None:
        diff = compute_diff({"name": "X"}, {"name": "X", "sex_id": "M"})
        assert diff == {"sex_id": {"old": None, "new": "M"}}

    def test_field_removed(self) -> None:
        diff = compute_diff({"name": "X", "sex_id": "M"}, {"name": "X"})
        assert diff == {"sex_id": {"old": "M", "new": None}}

    def test_none_vs_empty_list(self) -> None:
        diff = compute_diff({"nationalities": None}, {"nationalities": []})
        assert "nationalities" in diff

    def test_empty_dicts_equal(self) -> None:
        assert compute_diff({}, {}) == {}


class TestCanonical:
    def test_list_sorted(self) -> None:
        assert _canonical(["b", "a"]) == _canonical(["a", "b"])

    def test_nested_dict_sorted(self) -> None:
        assert _canonical({"z": 1, "a": 2}) == _canonical({"a": 2, "z": 1})
