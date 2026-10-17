import pytest
from conftest import TEST_DATABASE_URL

from api.admin import run


def test_created_key_works_and_can_be_revoked(client):
    output = run(["create-key", "ana", "build101"], TEST_DATABASE_URL)
    key = next(line[4:] for line in output.splitlines() if line.startswith("key="))
    headers = {"Authorization": f"Bearer {key}"}

    assert client.get("/me", headers=headers).json() == {"user": "ana", "team": "build101"}
    assert "ana\tbuild101" in run(["list-keys"], TEST_DATABASE_URL)

    key_id = output.split()[0].removeprefix("id=")
    assert run(["revoke-key", key_id], TEST_DATABASE_URL) == f"Revoked key {key_id}."
    assert client.get("/me", headers=headers).status_code == 401


def test_invalid_names_and_commands_are_rejected(client):
    with pytest.raises(SystemExit):
        run(["create-key", "ana; DROP TABLE api_keys"], TEST_DATABASE_URL)
    with pytest.raises(SystemExit):
        run(["delete-everything"], TEST_DATABASE_URL)
