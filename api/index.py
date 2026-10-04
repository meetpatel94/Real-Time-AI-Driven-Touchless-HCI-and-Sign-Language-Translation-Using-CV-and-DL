"""Vercel Python entry point for the Flask application.

Vercel detects this file as a Python Serverless Function. The exported ``app``
object is the existing Flask app, not a separate demo implementation.
"""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app import app  # noqa: E402,F401 - Vercel imports this WSGI application
