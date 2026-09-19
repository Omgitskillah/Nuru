from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest

from deep_think_mcp.file_cache import resolve_file_id


@pytest.fixture
def provider():
    p = MagicMock()
    p.upload_file.return_value = {"id": "file_1", "expires_at": None}
    return p


@pytest.fixture
def db_path(tmp_path):
    return tmp_path / "cache.db"


def _future_iso(seconds: int = 3600) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=seconds)).isoformat()


def _past_iso(seconds: int = 3600) -> str:
    return (datetime.now(timezone.utc) - timedelta(seconds=seconds)).isoformat()


def test_uploads_a_new_file_and_stores_it(provider, db_path, tmp_path):
    target = tmp_path / "a.txt"
    target.write_text("hello")

    file_id = resolve_file_id(provider, db_path, str(target))

    assert file_id == "file_1"
    provider.upload_file.assert_called_once_with(str(target.resolve()))


def test_reuses_the_cached_file_id_when_unchanged_and_not_expired(provider, db_path, tmp_path):
    target = tmp_path / "a.txt"
    target.write_text("hello")
    provider.upload_file.return_value = {"id": "file_1", "expires_at": _future_iso()}

    first = resolve_file_id(provider, db_path, str(target))
    second = resolve_file_id(provider, db_path, str(target))

    assert first == second == "file_1"
    provider.upload_file.assert_called_once()


def test_reuses_the_cached_file_id_when_it_never_expires(provider, db_path, tmp_path):
    target = tmp_path / "a.txt"
    target.write_text("hello")
    provider.upload_file.return_value = {"id": "file_1", "expires_at": None}

    resolve_file_id(provider, db_path, str(target))
    resolve_file_id(provider, db_path, str(target))

    provider.upload_file.assert_called_once()


def test_reuploads_when_the_file_content_changed(provider, db_path, tmp_path):
    target = tmp_path / "a.txt"
    target.write_text("hello")
    provider.upload_file.return_value = {"id": "file_1", "expires_at": _future_iso()}
    resolve_file_id(provider, db_path, str(target))

    target.write_text("goodbye")
    provider.upload_file.return_value = {"id": "file_2", "expires_at": _future_iso()}
    file_id = resolve_file_id(provider, db_path, str(target))

    assert file_id == "file_2"
    assert provider.upload_file.call_count == 2


def test_reuploads_when_the_cached_entry_expired(provider, db_path, tmp_path):
    target = tmp_path / "a.txt"
    target.write_text("hello")
    provider.upload_file.return_value = {"id": "file_1", "expires_at": _past_iso()}
    resolve_file_id(provider, db_path, str(target))

    provider.upload_file.return_value = {"id": "file_2", "expires_at": _future_iso()}
    file_id = resolve_file_id(provider, db_path, str(target))

    assert file_id == "file_2"
    assert provider.upload_file.call_count == 2


def test_raises_on_missing_file_without_calling_provider(provider, db_path, tmp_path):
    with pytest.raises(FileNotFoundError):
        resolve_file_id(provider, db_path, str(tmp_path / "does-not-exist.txt"))

    provider.upload_file.assert_not_called()
