import os

import uvicorn

from opcrew_backend.app import create_app


def build_app():
    return create_app()


app = build_app()


if __name__ == "__main__":
    reload_enabled = os.environ.get("OPENCREW_BACKEND_RELOAD", "").lower() in {"1", "true", "yes"}
    host = os.environ.get("OPENCREW_BACKEND_HOST", "127.0.0.1")
    port = int(os.environ.get("OPENCREW_BACKEND_PORT", "8011"))
    uvicorn.run("main:app", host=host, port=port, reload=reload_enabled)
