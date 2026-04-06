import uuid
import pytest
from unittest.mock import MagicMock, patch
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from src.main import app
from src.database import get_db
from src.auth import get_current_user
from src.module.models import User


#  helpers

def make_user(**kwargs) -> User:
    """Create a mock User ORM object."""
    user = MagicMock(spec=User)
    user.id = kwargs.get("id", uuid.uuid4())
    user.full_name = kwargs.get("full_name", "Felix Doe")
    user.email = kwargs.get("email", "felix@example.com")
    user.university_id = kwargs.get("university_id", "UNI-001")
    user.role = kwargs.get("role", "student")
    user.bio = kwargs.get("bio", "Test bio")
    user.sessionToken = kwargs.get("sessionToken", "valid-token")
    return user


CURRENT_USER = make_user()


def override_get_current_user():
    """Bypass JWT auth — return a fixed mock user."""
    return CURRENT_USER


def override_get_db():
    """Return a MagicMock session — no real DB needed."""
    db = MagicMock(spec=Session)
    yield db


app.dependency_overrides[get_current_user] = override_get_current_user
app.dependency_overrides[get_db] = override_get_db

client = TestClient(app)


# Users — /users/search

class TestSearchUsers:

    def _mock_db(self, results: list):
        """Wire the DB mock so query().filter().limit().all() returns results."""
        db = MagicMock(spec=Session)
        mock_query = db.query.return_value
        mock_query.filter.return_value = mock_query
        mock_query.limit.return_value = mock_query
        mock_query.all.return_value = results
        app.dependency_overrides[get_db] = lambda: (yield db)
        return db

    def teardown_method(self):
        # restore plain mock after each test
        app.dependency_overrides[get_db] = override_get_db

    def test_search_returns_matching_users(self):
        other = make_user(
            id=uuid.uuid4(),
            full_name="Alice Green",
            email="alice@example.com",
            university_id="UNI-002",
        )
        self._mock_db([other])

        response = client.get("/users/search?query=Alice")

        assert response.status_code == 200
        data = response.json()
        assert len(data) == 1
        assert data[0]["full_name"] == "Alice Green"

    def test_search_returns_empty_list_when_no_match(self):
        self._mock_db([])

        response = client.get("/users/search?query=nobody")

        assert response.status_code == 200
        assert response.json() == []

    def test_search_requires_query_param(self):
        response = client.get("/users/search")
        assert response.status_code == 422  # FastAPI validation error

    def test_search_rejects_query_shorter_than_2_chars(self):
        response = client.get("/users/search?query=a")
        assert response.status_code == 422



# Users — /users/{user_id}


class TestGetUserProfile:

    def _mock_db_for_get(self, result):
        db = MagicMock(spec=Session)
        mock_query = db.query.return_value
        mock_query.filter.return_value = mock_query
        mock_query.first.return_value = result
        app.dependency_overrides[get_db] = lambda: (yield db)
        return db

    def teardown_method(self):
        app.dependency_overrides[get_db] = override_get_db

    def test_get_existing_user_returns_profile(self):
        target_id = uuid.uuid4()
        target = make_user(id=target_id, full_name="Bob Jones", email="bob@example.com")
        self._mock_db_for_get(target)

        response = client.get(f"/users/{target_id}")

        assert response.status_code == 200
        assert response.json()["full_name"] == "Bob Jones"

    def test_get_nonexistent_user_returns_404(self):
        self._mock_db_for_get(None)

        response = client.get(f"/users/{uuid.uuid4()}")

        assert response.status_code == 404
        assert response.json()["detail"] == "User not found"



# Assistant — /ask


class TestAskEndpoint:

    def test_ask_returns_answer(self):
        mock_result = {
            "answer": "The library opens at 8am.",
            "intent": "knowledge",
            "sources": [],
        }

        with patch("src.module.assistant_views.llm_run", return_value=mock_result):
            response = client.post("/ask", json={
                "question": "What time does the library open?",
                "conversation_id": None,
                "user_id": str(CURRENT_USER.id),
                "channel": "web",
            })

        assert response.status_code == 200
        data = response.json()
        assert "answer" in data
        assert "library" in data["answer"].lower()

    def test_ask_strips_markdown_from_answer(self):
        mock_result = {
            "answer": "**Bold text** and [a link](https://example.com) here.",
            "intent": "knowledge",
            "sources": [],
        }

        with patch("src.module.assistant_views.llm_run", return_value=mock_result):
            response = client.post("/ask", json={
                "question": "Tell me something",
                "user_id": None,
                "channel": "web",
            })

        assert response.status_code == 200
        answer = response.json()["answer"]
        # markdown bold and link syntax should be removed
        assert "**" not in answer
        assert "](https://" not in answer

    def test_ask_requires_question_field(self):
        response = client.post("/ask", json={})
        assert response.status_code == 422

    def test_ask_with_conversation_id(self):
        conv_id = str(uuid.uuid4())
        mock_result = {"answer": "Here is your answer.", "intent": "knowledge"}

        with patch("src.module.assistant_views.llm_run", return_value=mock_result) as mock_llm:
            response = client.post("/ask", json={
                "question": "What modules do I have?",
                "conversation_id": conv_id,
                "user_id": str(CURRENT_USER.id),
                "channel": "web",
            })

        assert response.status_code == 200
        # confirm the conversation_id was forwarded to llm_run
        call_kwargs = mock_llm.call_args.kwargs
        assert call_kwargs["conversation_id"] == conv_id



# Assistant — detect_intent (unit tests, no HTTP)


class TestDetectIntent:

    def test_detects_timetable_intent(self):
        from src.module.assistant_service import detect_intent
        assert detect_intent("What classes do I have today?") == "timetable"
        assert detect_intent("Show me my timetable") == "timetable"
        assert detect_intent("Do I have a lecture tomorrow?") == "timetable"

    def test_detects_knowledge_intent(self):
        from src.module.assistant_service import detect_intent
        assert detect_intent("What is the library address?") == "knowledge"
        assert detect_intent("How do I reset my password?") == "knowledge"
