# client2_sse.py  ─────────────────────────────────────────────────────────────
import asyncio
import json
import os
import re
from contextlib import AsyncExitStack
from typing import Optional

import httpx
from dotenv import load_dotenv
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client
from mcp.types import JSONRPCNotification

from azure.identity import DefaultAzureCredential, get_bearer_token_provider
from openai import AzureOpenAI, AsyncAzureOpenAI
import uuid
from sseclient import SSEClient
import sys
# ─── credentials / LLM setup (unchanged) ────────────────────────────────────
load_dotenv()
endpoint    = os.getenv("ENDPOINT_URL",    "https://aihub6750316290.cognitiveservices.azure.com/")
deployment  = os.getenv("DEPLOYMENT_NAME", "gpt-4o")
token_prov  = get_bearer_token_provider(DefaultAzureCredential(), "https://cognitiveservices.azure.com/.default")

client      = AzureOpenAI(azure_endpoint=endpoint, azure_ad_token_provider=token_prov,
                          api_version="2025-01-01-preview")
aoai_client = AsyncAzureOpenAI(azure_endpoint=endpoint, azure_ad_token_provider=token_prov,
                               api_version="2024-02-15-preview")


apim_subscription_key = "YOUR_SUBSCRIPTION_KEY"  # replace with your Azure API Management subscription key

#MCP_ENDPOINT = "http://localhost:8000/mcp"
#MCP_ENDPOINT = "https://anildwamcpserver.wonderfultree-ede57f11.westus.azurecontainerapps.io/mcp"
MCP_ENDPOINT = "https://anildwaapiwestus-standard.azure-api.net/mcp"

sse_ready = asyncio.Event()

def ainput(prompt: str = "") -> asyncio.Future:
    """
    Non-blocking replacement for built-in input().
    Runs input(...) in a thread so the event-loop stays responsive.
    """
    return asyncio.to_thread(input, prompt)

# ─── helper to consume the SSE stream ───────────────────────────────────────
async def progress_listener(session_id: str) -> None:
    headers = {
        "Mcp-Session-Id": session_id,
        "Accept": "text/event-stream",
        "Cache-Control": "no-cache",
        "Ocp-Apim-Subscription-Key": apim_subscription_key
    }

    # 30-second connect timeout; no read/write deadlines (SSE is long-lived)
    timeout = httpx.Timeout(connect=30.0, read=None, write=None, pool=None)
    transport = httpx.AsyncHTTPTransport(retries=0)
    backoff = 2           # seconds – used only if initial attempt fails
    while True:
        try:
            async with httpx.AsyncClient(timeout=timeout, transport=transport, http2=False) as client:
                async with client.stream(
                    "GET", MCP_ENDPOINT, headers=headers
                ) as resp:
                    buffer: list[str] = []
                    async for raw_line in resp.aiter_lines():
                        if raw_line == "":
                            payload = "\n".join(buffer); buffer.clear()
                            print(f"payload >> {payload}", flush=True)
                            m = re.search(r"^data:\s*(.*)", payload, re.M)
                            if m:
                                root = json.loads(m.group(1))
                                if (
                                    root.get("method") == "notifications/progress"
                                    and "params" in root
                                ):
                                    pct = root["params"]["progress"]
                                    print(f"<< slow_count {pct:.0%}", file=sys.stderr)
                            continue
                        buffer.append(raw_line)
        except (httpx.ConnectTimeout, httpx.ReadTimeout) as exc:
            print("[progress-listener] timeout:", exc)
        except Exception as exc:
            print("[progress-listener] error:", exc)

        # If we drop out of the loop (connection closed or failed) → retry
        print(f"[progress-listener] reconnecting in {backoff}s …")
        await asyncio.sleep(backoff)
        backoff = min(backoff * 2, 30)    # exponential back-off, max 30 s

# ─── MCP client class ───────────────────────────────────────────────────────
class MCPClient:
    def __init__(self) -> None:
        self.exit_stack = AsyncExitStack()
        self.session: Optional[ClientSession] = None

    # -------------------------------------------------------------------- connect
    # client3.py  (only the lines that change)  ──────────────────────────────────
    

    async def connect_to_streamable_http_server(self) -> None:
        self.session_id = str(uuid.uuid4())

        # 2️⃣ JSON-RPC channel: POST /mcp  --------------------------------------
        client_cm = streamablehttp_client(
            url=MCP_ENDPOINT, #"http://localhost:8000/mcp",
            headers={"Mcp-Session-Id": self.session_id, "Ocp-Apim-Subscription-Key": apim_subscription_key},    
        )
        read, write, _ = await self.exit_stack.enter_async_context(client_cm)

        self.session = await self.exit_stack.enter_async_context(ClientSession(read, write))
        await self.session.initialize()
        await self.session.send_ping()
        #listener_task = asyncio.to_thread(listen, self.session_id)
        #await asyncio.wait_for(listener_task, timeout=15)
        # 3️⃣ SSE channel: GET /mcp  -------------------------------------------
        asyncio.create_task(progress_listener(self.session_id))

        # 4️⃣ discover tools, etc. ---------------------------------------------
        tools_rsp = await self.session.list_tools()
        print(
            "\nConnected to Streamable HTTP server with tools:",
            [t.name for t in tools_rsp.tools],
        )


    # --------------------------------------------------------------- process_query
    async def process_query(self, query: str) -> str:
        """
        Exactly the same logic you already wrote, minus any read.clone()
        or watch_sse().  Nothing here touches the SSE listener.
        """
        messages = [{"role": "user", "content": query}]
        tools_rsp = await self.session.list_tools()
        available_tools = [
            {
                "type": "function",
                "function": {
                    "name": t.name,
                    "description": t.description,
                    "parameters": t.inputSchema,
                },
            }
            for t in tools_rsp.tools
        ]

        # --- call the model --------------------------------------------------
        response = await aoai_client.chat.completions.create(
            model=deployment,
            messages=messages,
            max_tokens=800,
            temperature=0.7,
            top_p=0.95,
            tools=available_tools,
        )
        choice   = response.choices[0]
        message  = choice.message

        final_text = []
        if getattr(message, "tool_calls", None):
            for tc in message.tool_calls:
                tool_name = tc.function.name
                tool_args = json.loads(tc.function.arguments)

                print(f"Calling tool: {tool_name} with args: {tool_args}")

                if tool_name == "slow_count":
                    ack = await self.session.call_tool(tool_name, tool_args)
                    print(ack.content[0].text) 
                    continue

                result = await self.session.call_tool(tool_name, tool_args)

                # feed tool result back to the model …
                messages.extend(
                    [
                        {
                            "role": "assistant",
                            "tool_calls": [
                                {
                                    "id": tc.id,
                                    "type": "function",
                                    "function": {
                                        "name": tool_name,
                                        "arguments": json.dumps(tool_args),
                                    },
                                }
                            ],
                        },
                        {
                            "role": "tool",
                            "tool_call_id": tc.id,
                            "content": result.content,
                        },
                    ]
                )

                follow_up = await aoai_client.chat.completions.create(
                    model=deployment,
                    messages=messages,
                    max_tokens=800,
                    temperature=0.7,
                    top_p=0.95,
                    tools=available_tools,
                )
                final_text.append(follow_up.choices[0].message.content)
        else:
            final_text.append(message.content)

        return "\n".join(final_text)

    # ---------------------------------------------------------------- chat loop
    async def chat_loop(self) -> None:
        print("\nMCP Client Started!  Type your queries or 'quit' to exit.")
        while True:
            #q = input("\nQuery: ").strip()
            q = (await ainput("\nQuery: ")).strip() 
            if q.lower() == "quit":
                break
            try:
                print("\n" + await self.process_query(q))
            except Exception as exc:
                print("Error:", exc)

    # ---------------------------------------------------------------- cleanup
    async def cleanup(self) -> None:
        await self.exit_stack.aclose()



# ─── main ────────────────────────────────────────────────────────────────────
async def main() -> None:
    cli = MCPClient()
    try:
        await cli.connect_to_streamable_http_server()
        await cli.chat_loop()
    finally:
        await cli.cleanup()

if __name__ == "__main__":
    asyncio.run(main())
