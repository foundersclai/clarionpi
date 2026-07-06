"""ClarionPI backend entrypoint — minimal FastAPI app for M0 scaffold."""

from fastapi import FastAPI

app = FastAPI(title="ClarionPI")


@app.get("/healthz")
def healthz() -> dict[str, str]:
    """Liveness probe."""
    return {"status": "ok"}
