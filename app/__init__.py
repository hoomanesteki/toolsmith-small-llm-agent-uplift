"""The control plane: one process serving the API, the event stream and the UI.

One port, one URL, no CORS. See :mod:`app.main` for why that is a deployment
decision rather than only an architectural one.
"""

from app.main import app

__all__ = ["app"]
