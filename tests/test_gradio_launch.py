"""Start the Gradio console and request the local page."""

import urllib.request

from app import build_app


def test_gradio_page_starts():
    demo = build_app()
    demo.launch(server_name="127.0.0.1", server_port=7861, prevent_thread_lock=True, quiet=True)
    try:
        with urllib.request.urlopen("http://127.0.0.1:7861/", timeout=20) as response:
            body = response.read()
            assert response.status == 200
            assert len(body) > 100
    finally:
        demo.close()
