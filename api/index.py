"""Vercel Python Function entrypoint for CineSphere."""

from cinesphere.app import create_app


app = create_app("production")
