from app.agent.orchestrator import process_message


def test_process_message_returns_string():
    response = process_message(user_id="test_user", message="Hello")
    assert isinstance(response, str), "process_message must return a string"
    assert len(response) > 0, "Response must not be empty"


def test_process_message_no_image():
    response = process_message(user_id="test_user_2", message="Show me some art", image_url=None)
    assert isinstance(response, str)
