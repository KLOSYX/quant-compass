import requests
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
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
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router, prefix="/api")
app.mount("/", StaticFiles(directory="static", html=True), name="static")
