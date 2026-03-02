# Wang — Knowledge Base

- Agent name: **wang**
- Project: THE-Seed-OpenRA

## agent-chat Communication
- MCP server `agent-chat` is connected and working (5 tools: whoami, check_inbox, send_message, post, check_group)
- If MCP is missing, check if it was accidentally disabled in the MCP dialog first before assuming systemic issue
- curl REST API fallback available if needed:
  - `$AGENT_CHAT_API` = remote backend URL, `$API_TOKEN` = bearer auth
  - Inbox: `GET /api/inbox/wang`, Send: `POST /api/messages`
