import pytest
import requests


@pytest.mark.parametrize("method", ["GET", "POST"])
def test_unmocked_session_requests_fail_before_transport(method):
    with requests.Session() as session:
        with pytest.raises(pytest.fail.Exception, match="Unmocked requests network access"):
            session.request(method, "https://example.invalid/", timeout=0.1)


def test_registered_mock_response_is_still_available(requests_mock):
    requests_mock.get("https://example.invalid/", text="mocked response")

    response = requests.get("https://example.invalid/", timeout=0.1)

    assert response.text == "mocked response"
    assert requests_mock.call_count == 1
