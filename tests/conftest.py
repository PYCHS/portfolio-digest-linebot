import pytest
from requests.adapters import HTTPAdapter


@pytest.fixture(autouse=True)
def block_unmocked_requests(monkeypatch):
    """Keep the suite offline even when a test forgets requests-mock."""
    def blocked_send(*_args, **_kwargs):
        pytest.fail(
            "Unmocked requests network access blocked; register a requests-mock response.",
            pytrace=False,
        )

    # requests-mock installs its own adapter, so registered mocks still work.
    monkeypatch.setattr(HTTPAdapter, "send", blocked_send)
