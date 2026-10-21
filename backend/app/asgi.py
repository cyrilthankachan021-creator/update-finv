"""ASGI entry point: `uvicorn app.asgi:app`. Run `alembic upgrade head` first."""
from .main import create_app

app = create_app()
