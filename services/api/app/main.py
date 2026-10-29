"""Local ASGI entry point; the same app runs on Vercel."""

from services.serverless.app import app

__all__ = ["app"]
