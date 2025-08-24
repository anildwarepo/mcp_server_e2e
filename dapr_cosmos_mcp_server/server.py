"""
Run:  uvicorn server:app --port 8000 --reload
dapr: dapr run --app-id cosmos_dapr_actor --dapr-http-port 3500 --app-port 3000 -- uvicorn --port 3000 server:app
"""

import asyncio, json, uuid, socket, os, inspect
from typing import Annotated, Any, Optional, Dict
from fastapi import FastAPI, Request, BackgroundTasks, Response
from fastapi.responses import StreamingResponse, JSONResponse
import httpx
from contextlib import asynccontextmanager
from tools import REGISTERED_TOOLS, TOOL_FUNCS, tool
from cosmosdb_helper import cosmosdb_create_item, ensure_container_exists, cosmosdb_query_items
from task_manager_actor import TaskManagerActor  # type: ignore 
from backup_actor import BackupActor  # type: ignore
from dapr.ext.fastapi import DaprActor  # type: ignore
from dapr.actor import ActorProxy, ActorId
from task_manager_actor_interface import TaskManagerActorInterface


POD = socket.gethostname()
REV = os.getenv("CONTAINER_APP_REVISION", "unknown")

@asynccontextmanager
async def lifespan(app: FastAPI):
    await actor.register_actor(TaskManagerActor)
    await actor.register_actor(BackupActor)
    await ensure_container_exists()
    yield


app = FastAPI(lifespan=lifespan)
actor = DaprActor(app)

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


# ───────────────── tools (unchanged API) ─────────────────────────────────────
@tool
async def create_item(item: Annotated[dict, "Json object to be inserted"]) -> Annotated[str, "create_item Result"]:
    """
    Create a new item in the Cosmos DB container.
    item is a Json object.
    """
    try:
        response = await cosmosdb_create_item(item)
        print("Created item:", response)
        return "Item created successfully"
    except Exception as e:
        print("Error creating item:", e)
        return "Error creating item"

@tool
async def query_backup_tasks(cosmosDbQuery: Annotated[str, "Cosmos DB SQL query that maps to user query"]) -> Annotated[list[dict], "query_backup_tasks Result"]:
    """
    Query backup tasks from the Cosmos DB container.

    backup Item schema:
        {{
        "user_id": "user id provided to you",
        "id": "<GUID>",
        "task": "Backup files",
        "files": ["x", "y", "z"],
        "servers": ["server 1", "server 2", "server 3"],
        "backup_frequency_path": "P1W"
        }}
    User the user_id provided to you to query the backup tasks. 
    """
    try:
        items = await cosmosdb_query_items(cosmosDbQuery)
        print("Queried items:", items)
        return items
    except Exception as e:
        print("Error querying items:", e)
        return []

@tool
async def setup_backup_task_agent(user_id: Annotated[str, "User ID for the backup task"]) -> Annotated[str, "setup_backup_task_agent Result"]:
    """
    Set up the backup task agent for the user.
    """
    try:
        proxy = ActorProxy.create('TaskManagerActor', ActorId(user_id), TaskManagerActorInterface)
        await proxy.SetReminder(True)
        return "Backup task agent set up successfully"
    except Exception as e:
        print("Error setting up backup task agent:", e)
        return "Error setting up backup task agent"



@tool
async def slow_count(n: int, session_id: Optional[str] = None) -> dict:
    assert session_id, "slow_count requires a session_id"
    token = f"slow_count/{session_id}"

    async def _runner():
        for i in range(1, n + 1):

            print(f"slow_count: step {i} of {n} (session {session_id})", flush=True)
            # progress event (Inspector shows progress bar if recognized)
            progress_msg = sse_event(
                {
                    "jsonrpc": JSONRPC,
                    "method": "notifications/progress",
                    "params": {"progressToken": token, "progress": i / n},
                }
            )
            await SESSIONS.publish(session_id, progress_msg)

            # custom notification (will show in Server Notifications panel)
            #notify_msg = sse_event(
            #    {
            #        "jsonrpc": JSONRPC,
            #        "method": "notifications/progress",
            #        "params": {"step": i, "of": n, "status": "in-progress"},
            #    }
            #)

            notify_msg = sse_event(
                {
                    "jsonrpc": JSONRPC,
                    "method": "notifications/message",
                    "params": {
                        "progress": i / n,
                        "level": "info",
                        "data": [
                            {"type": "text", "text": f"slow_count: step {i} of {n} (session {session_id})"}
                        ],
                    },
                }
            )
            await SESSIONS.publish(session_id, notify_msg)

            await asyncio.sleep(1)

        # final “done” progress
        done_msg = sse_event(
            {
                "jsonrpc": JSONRPC,
                "method": "notifications/progress",
                "params": {"progressToken": token, "progress": 1.0},
            }
        )
        await SESSIONS.publish(session_id, done_msg)

        # final custom notification
        #final_notify = sse_event(
        #    {
        #        "jsonrpc": JSONRPC,
        #        "method": "notifications/progress",
        #        "params": {"status": "done"},
        #    }
        #)

        final_notify = sse_event(
            {
                "jsonrpc": JSONRPC,
                "method": "notifications/message",
                "params": {
                    "level": "info",
                    "data": [
                        {"type": "text", "text": f"slow_count done (n={n}, session {session_id})"}
                    ],
                },
            }
        )
        await SESSIONS.publish(session_id, final_notify)

    asyncio.create_task(_runner())
    return {
        "content": [{"type": "text", "text": f"Started slow_count({n}), token={token}"}]
    }



# ─────────────── call_tool wrapper ensures session_id injection ──────────────
async def call_tool(name: str, raw_args: dict, tasks: BackgroundTasks, session_id: str):
    
    if name not in TOOL_FUNCS:
        return "Error: Tool not found"

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

# ───────────────── health check ─────────────────────────────────────────────
@app.get("/status")
async def status(request: Request):
    return {"status": "ok"}

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
                "serverInfo": {"name": "fastapi-mcp", "version": "0.1"},
                "capabilities": {"tools": {"listChanged": True, "callTool": True}}, #{"listTools": True, "toolCalling": True, "sse": True},
            }

        case "ping" | "$/ping":
            result = {} #{"pong": True}

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
