"""Routes: the HTTP layer. Each handler validates input, checks auth, and calls a controller."""
from app.routes import auth, internal, podcast, scheduler_runs, schedulers

ALL_ROUTERS = [auth.router, schedulers.router, scheduler_runs.router, podcast.router, internal.router]
