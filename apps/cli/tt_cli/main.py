"""`tt` reporter CLI. Phase 0 only provides the entrypoint; commands arrive in Phase 2."""

import typer

app = typer.Typer(help="Team Tracker reporter CLI")


@app.callback()
def _root() -> None:
    """Team Tracker reporter CLI."""
