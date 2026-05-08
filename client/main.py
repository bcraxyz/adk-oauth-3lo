from __future__ import annotations

import json
import logging
import os
import sys

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, StreamingResponse

load_dotenv(dotenv_path="../.env")

logging.basicConfig(
    level=logging.INFO, stream=sys.stdout, format="%(levelname)s: %(message)s"
)
logger = logging.getLogger("adk_oauth_3lo." + __name__)

app = FastAPI()

AGENT_URL = os.environ.get("AGENT_BACKEND_URL", "http://localhost:8000")
APP_NAME = "adk_oauth_3lo"

# Server-side nonce store: user_id -> {"nonce": str, "invocation_id": str}
# Populated by intercepting adk_request_credential events in the SSE stream.
# Only the FIRST nonce per user is stored — subsequent ones are ignored.
pending_auth: dict[str, dict] = {}


@app.get("/")
def ui():
    with open("index.html", "r") as f:
        return HTMLResponse(content=f.read())


@app.post("/chat")
async def chat(request: Request):
    data = await request.json()
    message = data.get("message")
    function_response = data.get("function_response")

    user_id = data.get("user_id", "test_user")
    session_id = data.get("session_id", "default_session_id")

    payload = {
        "appName": APP_NAME,
        "userId": user_id,
        "sessionId": session_id,
        "streaming": True,
    }

    if message:
        payload["newMessage"] = {
            "role": "user",
            "parts": [{"text": message}],
        }
        # Clear any pending auth state for this user on a new message
        pending_auth.pop(user_id, None)
    elif function_response:
        payload["newMessage"] = {
            "role": "user",
            "parts": [{"functionResponse": function_response}],
        }

    async def proxy_stream():
        async with httpx.AsyncClient(timeout=120.0) as client:
            await client.post(
                f"{AGENT_URL}/apps/{APP_NAME}/users/{user_id}/sessions/{session_id}"
            )
            async with client.stream(
                "POST", f"{AGENT_URL}/run_sse", json=payload
            ) as r:
                if r.status_code != 200:
                    err = await r.aread()
                    yield f"data: {json.dumps({'error': err.decode()})}\n\n"
                    return
                async for line in r.aiter_lines():
                    if line:
                        json_str = line[6:] if line.startswith("data: ") else line
                        if json_str.startswith("{"):
                            try:
                                ev = json.loads(json_str)
                                parts = (ev.get("content") or {}).get("parts") or []
                                for p in parts:
                                    fc = p.get("functionCall") or p.get("function_call")
                                    if fc and fc.get("name") == "adk_request_credential":
                                        if user_id not in pending_auth:
                                            args = fc.get("args") or {}
                                            auth_config = args.get("authConfig") or args.get("auth_config") or {}
                                            exchanged = auth_config.get("exchangedAuthCredential") or auth_config.get("exchanged_auth_credential") or {}
                                            oauth2 = exchanged.get("oauth2") or {}
                                            nonce = oauth2.get("nonce")
                                            invocation_id = ev.get("invocationId") or ev.get("invocation_id")
                                            if nonce:
                                                pending_auth[user_id] = {"nonce": nonce, "invocation_id": invocation_id}
                                                logger.info(f"Stored nonce and invocation_id={invocation_id} for user {user_id}")
                            except Exception:
                                pass
                        yield f"{line}\n\n"

    return StreamingResponse(proxy_stream(), media_type="text/event-stream")


@app.api_route("/commit", methods=["GET"])
async def commit(request: Request):
    connector = request.query_params.get("connector_name")
    state = request.query_params.get("user_id_validation_state")

    user_id = request.cookies.get("user_id")
    entry = pending_auth.get(user_id) if user_id else None
    if not entry and pending_auth:
        user_id, entry = next(iter(pending_auth.items()))

    nonce = entry["nonce"] if entry else None
    logger.info(f"commit: user_id={user_id}, nonce_present={bool(nonce)}, connector={connector}")

    payload = {
        "userId": user_id,
        "userIdValidationState": state,
        "consentNonce": nonce,
    }

    url = f"https://iamconnectorcredentials.googleapis.com/v1alpha/{connector}/credentials:finalize"
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(url, json=payload)
            logger.info(f"finalize status={resp.status_code} body={resp.text[:200]}")
            resp.raise_for_status()
    except httpx.HTTPError as e:
        err_text = e.response.text if hasattr(e, "response") else str(e)
        status = e.response.status_code if hasattr(e, "response") else 500
        logger.error(f"Commit failed: {err_text}")
        return HTMLResponse(err_text, status_code=status)

    pending_auth.pop(user_id, None)

    return HTMLResponse("""
        <script>window.close();</script>
        <p>Success. You can close this window.</p>
    """)
