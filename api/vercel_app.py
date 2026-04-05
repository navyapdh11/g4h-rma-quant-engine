"""
Vercel Serverless Entry Point
==============================
Wraps the FastAPI app for Vercel's serverless Python runtime.
Note: Heavy numerical libs (numpy, scipy, arch) may exceed lambda size limits.
For production, use a Docker-based deployment (Render, Railway, etc.) instead.
"""
import sys
import os

# Add parent directory to path so imports resolve
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

# Import the FastAPI app
from api.app import app  # noqa: F401

# Vercel expects 'app' to be a WSGI/ASGI callable
# FastAPI (via Starlette) is ASGI-compatible
