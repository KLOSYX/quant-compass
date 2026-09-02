import requests
from pathlib import Path
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from starlette.staticfiles import StaticFiles

from api.routes import router

# Set a default timeout for all requests globally to prevent hanging
# This specifically addresses the user's request to "reduce timeout"
_original_request = requests.Session.request


def _patched_request(self, method, url, *args, **kwargs):
    if "timeout" not in kwargs:
        kwargs["timeout"] = 10  # 10 seconds timeout
    return _original_request(self, method, url, *args, **kwargs)


requests.Session.request = _patched_request

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://127.0.0.1:8666",
        "http://localhost:8666",
        "http://127.0.0.1:3000",
        "http://localhost:3000",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router, prefix="/api")
static_directory = Path(__file__).resolve().parent / "static"
if static_directory.is_dir():
    app.mount("/", StaticFiles(directory=static_directory, html=True), name="static")
else:

    @app.get("/", response_class=HTMLResponse)
    async def development_root():
        return "<html><body>Quant Compass API is running in research_only mode.</body></html>"
