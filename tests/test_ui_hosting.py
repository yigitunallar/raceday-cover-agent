from fastapi.testclient import TestClient
from raceday.api.app import app


def test_form_and_assets_are_served_from_same_app():
    client = TestClient(app)
    response = client.get('/', follow_redirects=False)
    assert response.status_code == 307
    assert response.headers['location'] == '/ui/mission.html'
    page = client.get(response.headers['location'])
    assert page.status_code == 200
    assert 'Tell us about your horse' in page.text
    assert client.get('/ui/mission.js').status_code == 200
    assert client.get('/ui/admin.html').status_code == 200
