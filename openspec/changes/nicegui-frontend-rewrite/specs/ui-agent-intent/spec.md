## ADDED Requirements

### Requirement: LLM function calling intent routing
The system SHALL maintain a UI Agent loop in the NiceGUI interface. User input in the chat box SHALL be sent to `LLMClient.chat(tools=<interview_control_tools>)`. If the LLM returns a `tool_call`, the system SHALL dispatch to `ToolRegistry` and execute the corresponding tool function. If no tool is called, the LLM's text reply SHALL be shown as an Agent bubble.

#### Scenario: User input triggers tool call
- **WHEN** user types "开始面试" and submits
- **THEN** LLM returns a `tool_call` for `start_interview`, the tool executes, and an Agent bubble confirms the action

#### Scenario: User input produces text reply
- **WHEN** user types a general question not matching any tool intent
- **THEN** LLM returns a plain text reply shown as an Agent bubble, no tool is executed

### Requirement: interview_control_tools registered in ToolRegistry
The system SHALL register the following tool functions in `src/tools/interview_control_tools.py` with `ToolRegistry`:

| Tool | Trigger | Internal call |
|------|---------|---------------|
| `start_interview(candidate_id)` | "开始面试"、"start" | `POST /api/interview/start` |
| `stop_interview()` | "结束面试"、"stop" | `POST /api/interview/stop` |
| `get_eval_report()` | "生成报告"、"看报告" | `GET /api/interview/eval` |
| `request_suggestion()` | "追问"、"建议" | `POST /api/interview/suggest` (or WS `request_suggestion`) |
| `regenerate_questions(candidate_id)` | "重新提炼问题" | `GET /api/resume/profile?candidate_id=<id>` |

Each function SHALL use `httpx.AsyncClient` to call the local REST API.

#### Scenario: start_interview tool executes correctly
- **WHEN** `start_interview` tool is dispatched with a valid `candidate_id`
- **THEN** `POST /api/interview/start` is called with the candidate_id and a success Agent bubble appears

#### Scenario: stop_interview triggers eval flow
- **WHEN** `stop_interview` tool is dispatched
- **THEN** `POST /api/interview/stop` is called; on success `get_eval_report` is automatically called and the report tab activates

#### Scenario: Tool call fails gracefully
- **WHEN** a tool function receives a non-2xx HTTP response
- **THEN** an error Agent bubble appears with the failure message; no exception propagates to the UI

### Requirement: Button actions share same tool path
The system SHALL ensure that UI buttons (e.g., "开始面试", "结束面试") invoke the same tool functions as the LLM function calling path, so buttons and natural language commands produce identical behavior.

#### Scenario: Start button equivalent to "开始面试" command
- **WHEN** user clicks the "开始面试" button
- **THEN** `start_interview` tool function is called directly, same as if routed via LLM
