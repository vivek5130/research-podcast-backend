"""Controllers: business logic. Called by routes, they call repositories, handlers and services.

They raise domain errors (``app.core.exceptions``) instead of HTTP errors, so they
can be reused and tested without FastAPI.
"""
