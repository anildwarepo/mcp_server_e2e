"""
Run:  uvicorn server:app --port 8000 --reload
"""

import asyncio, json, uuid, socket, os, inspect
from typing import Any, Optional, Dict
from fastapi import FastAPI, Request, BackgroundTasks, Response
from fastapi.responses import StreamingResponse, JSONResponse
import httpx

from tools import REGISTERED_TOOLS, TOOL_FUNCS, tool

POD = socket.gethostname()
REV = os.getenv("CONTAINER_APP_REVISION", "unknown")

app = FastAPI()
JSONRPC = "2.0"

# ───────────────── helpers ───────────────────────────────────────────────────
def sse_event(data: dict, event: str = "message") -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"

NWS_API_BASE = "https://api.weather.gov"
USER_AGENT = "weather-app/1.0"

# ──────────────── Async Session Manager (concurrent users) ───────────────────
class Session:
    def __init__(self, session_id: str) -> None:
        self.session_id = session_id
        self.q: asyncio.Queue[str] = asyncio.Queue()
        self.closed = False
        self.last_activity = asyncio.get_event_loop().time()

    def touch(self) -> None:
        self.last_activity = asyncio.get_event_loop().time()

    async def publish(self, msg: str) -> None:
        if not self.closed:
            await self.q.put(msg)
            self.touch()

    def close(self) -> None:
        self.closed = True

class SessionManager:
    def __init__(self) -> None:
        self._sessions: Dict[str, Session] = {}
        self._lock = asyncio.Lock()

    async def get_or_create(self, session_id: str) -> Session:
        async with self._lock:
            s = self._sessions.get(session_id)
            if s is None or s.closed:
                s = Session(session_id)
                self._sessions[session_id] = s
            return s

    async def publish(self, session_id: str, msg: str) -> None:
        s = await self.get_or_create(session_id)
        await s.publish(msg)

    async def delete(self, session_id: str) -> bool:
        async with self._lock:
            s = self._sessions.pop(session_id, None)
        if s:
            s.close()
            # drain queue to help gc
            while not s.q.empty():
                try:
                    s.q.get_nowait()
                    s.q.task_done()
                except Exception:
                    break
            return True
        return False

    async def exists(self, session_id: str) -> bool:
        async with self._lock:
            return session_id in self._sessions

SESSIONS = SessionManager()

# ───────────────── HTTP helpers ──────────────────────────────────────────────
async def make_nws_request(url: str) -> dict[str, Any] | None:
    headers = {"User-Agent": USER_AGENT, "Accept": "application/geo+json"}
    async with httpx.AsyncClient() as client:
        try:
            resp = await client.get(url, headers=headers, timeout=30.0)
            resp.raise_for_status()
            return resp.json()
        except Exception:
            return None

def format_alert(feature: dict) -> str:
    props = feature["properties"]
    return f"""
Event: {props.get('event', 'Unknown')}
Area: {props.get('areaDesc', 'Unknown')}
Severity: {props.get('severity', 'Unknown')}
Description: {props.get('description', 'No description available')}
Instructions: {props.get('instruction', 'No specific instructions provided')}
"""

# ───────────────── tools (unchanged API) ─────────────────────────────────────
@tool
async def get_alerts(state: str) -> str:
    """Get weather alerts for a US state (e.g., 'CA')."""
    url = f"{NWS_API_BASE}/alerts/active/area/{state}"
    data = await make_nws_request(url)
    if not data or "features" not in data:
        return "Unable to fetch alerts or no alerts found."
    if not data["features"]:
        return "No active alerts for this state."
    alerts = [format_alert(f) for f in data["features"]]
    return "\n---\n".join(alerts)

@tool
async def get_forecast(latitude: float, longitude: float) -> str:
    """Get NWS forecast for a location."""
    points_url = f"{NWS_API_BASE}/points/{latitude},{longitude}"
    points_data = await make_nws_request(points_url)
    if not points_data:
        return "Unable to fetch forecast data for this location."

    forecast_url = points_data["properties"]["forecast"]
    forecast_data = await make_nws_request(forecast_url)
    if not forecast_data:
        return "Unable to fetch detailed forecast."

    periods = forecast_data["properties"]["periods"]
    out = []
    for p in periods[:5]:
        out.append(
            f"""
{p['name']}:
Temperature: {p['temperature']}°{p['temperatureUnit']}
Wind: {p['windSpeed']} {p['windDirection']}
Forecast: {p['detailedForecast']}
"""
        )
    return "\n---\n".join(out)

@tool
async def slow_count(n: int, session_id: Optional[str] = None) -> dict:
    """
    Counts to n and streams MCP progress notifications to the session SSE channel.
    """
    assert session_id, "slow_count requires a session_id"
    token = f"slow_count/{session_id}"

    async def _runner():
        for i in range(1, n + 1):
            msg = sse_event(
                {
                    "jsonrpc": JSONRPC,
                    "method": "notifications/progress",
                    "params": {"progressToken": token, "progress": i / n},
                },
                "message",
            )
            await SESSIONS.publish(session_id, msg)
            await asyncio.sleep(1)

        # final “done”
        msg = sse_event(
            {
                "jsonrpc": JSONRPC,
                "method": "notifications/progress",
                "params": {"progressToken": token, "progress": 1.0},
            },
            "message",
        )
        await SESSIONS.publish(session_id, msg)

    asyncio.create_task(_runner())

    return {
        "content": [
            {"type": "text", "text": f"Started slow_count({n}), token={token}"}
        ]
    }

# ─────────────── call_tool wrapper ensures session_id injection ──────────────
async def call_tool(name: str, raw_args: dict, tasks: BackgroundTasks, session_id: str):
    fn  = TOOL_FUNCS[name]
    sig = inspect.signature(fn)
    args = dict(raw_args)
    if "session_id" in sig.parameters:
        args["session_id"] = session_id
    result = await fn(**args) if inspect.iscoroutinefunction(fn) else fn(**args)
    return result

def _ensure_calltool_result(obj):
    if isinstance(obj, dict) and "content" in obj:
        return obj
    return {"content": [{"type": "text", "text": str(obj)}]}

def _normalize_session_id(raw: str | None, default: str = "default") -> str:
    if not raw:
        return default
    return raw.split(",")[0].strip()

# ───────────────── SSE channel ───────────────────────────────────────────────
@app.get("/mcp")
async def mcp_sse(request: Request):
    session_id = _normalize_session_id(request.headers.get("Mcp-Session-Id"))
    print(f"[SSE OPEN] session={session_id} pod={POD} rev={REV}", flush=True)
    session = await SESSIONS.get_or_create(session_id)

    async def event_stream():
        # flush headers immediately (APIM/ACA friendly)
        yield "event: open\ndata: {}\n\n"

        heartbeat_every = 15.0  # seconds
        while True:
            if await request.is_disconnected():
                break
            try:
                # wait up to heartbeat interval for next message
                msg = await asyncio.wait_for(session.q.get(), timeout=heartbeat_every)
                yield msg
                session.q.task_done()
            except asyncio.TimeoutError:
                # heartbeat (SSE comment doesn't disturb clients)
                yield "server version 1.0: ping\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Content-Type-Options": "nosniff",
        },
    )

# ───────────────── JSON-RPC handler ──────────────────────────────────────────
@app.post("/mcp")
async def mcp_post(req: Request, tasks: BackgroundTasks):
    req_json   = await req.json()
    raw        = req.headers.get("Mcp-Session-Id")
    session_id = _normalize_session_id(raw, default=str(uuid.uuid4()))
    # ensure session exists for any tool that will stream
    await SESSIONS.get_or_create(session_id)

    method = req_json.get("method")
    rpc_id = req_json.get("id")
    print(f"[POST] method={method} session={session_id} pod={POD} rev={REV}", flush=True)

    match method:
        case "initialize":
            result = {
                "protocolVersion": "2025-03-26",
                "capabilities": {"listTools": True, "toolCalling": True, "sse": True},
                "serverInfo": {"name": "fastapi-mcp", "version": "0.1"},
            }

        case "ping" | "$/ping":
            result = {"pong": True}

        case "workspace/listTools" | "$/listTools" | "list_tools" | "tools/list":
            result = {"tools": REGISTERED_TOOLS}

        case "tools/call" | "$/call":
            tool_name = req_json["params"]["name"]
            raw_args  = req_json["params"].get("arguments", {})
            raw_out   = await call_tool(tool_name, raw_args, tasks, session_id)
            result    = _ensure_calltool_result(raw_out)

        case _ if method in TOOL_FUNCS:
            raw_args = req_json.get("params", {})
            raw_out  = await call_tool(method, raw_args, tasks, session_id)
            result   = _ensure_calltool_result(raw_out)

        case _:
            if rpc_id is None:
                return Response(status_code=202, headers={"Mcp-Session-Id": session_id})
            return JSONResponse(
                content={"jsonrpc": JSONRPC, "id": rpc_id,
                         "error": {"code": -32601, "message": "method not found"}},
                headers={"Mcp-Session-Id": session_id},
                background=tasks,
            )

    return JSONResponse(
        content={"jsonrpc": JSONRPC, "id": rpc_id, "result": result},
        headers={"Mcp-Session-Id": session_id},
        background=tasks,
    )

# ───────────────── session cleanup ───────────────────────────────────────────
@app.delete("/mcp")
async def mcp_delete(request: Request):
    session_id = _normalize_session_id(request.headers.get("Mcp-Session-Id"))
    if session_id:
        deleted = await SESSIONS.delete(session_id)
        return Response(status_code=204 if deleted else 404)
    return Response(status_code=404)
