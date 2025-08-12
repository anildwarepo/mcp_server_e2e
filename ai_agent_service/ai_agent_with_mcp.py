from azure.ai.projects import AIProjectClient
from azure.identity import DefaultAzureCredential
from azure.ai.agents.models import ListSortOrder
from azure.ai.agents.models import (MessageTextContent, 
                                    ListSortOrder, 
                                    McpTool, 
                                    SubmitToolApprovalAction, 
                                    RequiredMcpToolCall,
                                    ToolApproval)
import time
import json

mcp_server_label = "anildwa_mcp_server"
mcp_server_url = "https://anildwaapiwestus-standard.azure-api.net/mcp"
model_deployment_name = "gpt-4.1-mini"
mcp_tool = McpTool(
    server_label=mcp_server_label,
    server_url=mcp_server_url,
    allowed_tools=[],  # Optional
)

project_client = AIProjectClient(
    credential=DefaultAzureCredential(),
    endpoint="https://anildwa-ai-foundry-reso-resource.services.ai.azure.com/api/projects/anildwa-ai-foundry-resource01")

with project_client:
    agent = project_client.agents.create_agent(
        model=model_deployment_name,
        name="my-mcp-agent",
        instructions=(
            "You are a helpful assistant. Use the tools provided to answer the user's "
            "questions. Be sure to cite your sources."
        ),
        tools=mcp_tool.definitions
    )
    print(f"Created agent, agent ID: {agent.id}")


    thread = project_client.agents.threads.create()
    print(f"Created thread, thread ID: {thread.id}")

    message = project_client.agents.messages.create(
        thread_id=thread.id,
        role="user",
        content="slow count from 10"
    )
    print(f"Created message, message ID: {message.id}")

    run = project_client.agents.runs.create(
        thread_id=thread.id,
        agent_id=agent.id
    )

    while run.status in ["queued", "in_progress", "requires_action"]:
        time.sleep(1)
        run = project_client.agents.runs.get(thread_id=thread.id, run_id=run.id)
        if run.status == "requires_action" and isinstance(run.required_action, SubmitToolApprovalAction):
            tool_calls = run.required_action.submit_tool_approval.tool_calls
            if not tool_calls:
                print("No tool calls provided - cancelling run")
                project_client.runs.cancel(thread_id=thread.id, run_id=run.id)
                break

            tool_approvals = []
            for tool_call in tool_calls:
                if isinstance(tool_call, RequiredMcpToolCall):
                    try:
                        print(f"Approving tool call: {tool_call}")
                        tool_approvals.append(
                            ToolApproval(
                                tool_call_id=tool_call.id,
                                approve=True,
                                headers=mcp_tool.headers,
                            )
                        )
                    except Exception as e:
                        print(f"Error approving tool_call {tool_call.id}: {e}")

            print(f"tool_approvals: {tool_approvals}")
            if tool_approvals:
                project_client.agents.runs.submit_tool_outputs(
                    thread_id=thread.id,
                    run_id=run.id,
                    tool_approvals=tool_approvals
                )

        print(f"Current run status: {run.status}")

        # Retrieve the generated response:
        messages = project_client.agents.messages.list(thread_id=thread.id)
        print("\nConversation:")
        print("-" * 50)

        for msg in messages:
            if msg.text_messages:
                last_text = msg.text_messages[-1]
                print(f"{msg.role.upper()}: {last_text.text.value}")
                print("-" * 50)
