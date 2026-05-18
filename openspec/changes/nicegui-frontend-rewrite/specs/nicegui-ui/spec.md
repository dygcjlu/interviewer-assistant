## ADDED Requirements

### Requirement: Single-page Agent dialog layout
The system SHALL render a single-page NiceGUI interface mounted to the existing FastAPI app, with a top status bar, a main Agent chat area (60% width), and a collapsible right panel (40% width). No client-side routing is used.

#### Scenario: Page loads on startup
- **WHEN** user navigates to `http://127.0.0.1:8000`
- **THEN** the NiceGUI page renders with top status bar, empty chat area, and right panel visible

#### Scenario: Top status bar reflects session state
- **WHEN** a `session_snapshot` WebSocket event is received
- **THEN** the top bar updates the interview stage badge, candidate name, and current round number

### Requirement: Agent chat area with typed message bubbles
The system SHALL display chat messages in a scrollable area using distinct bubble styles: Agent messages (left-aligned), user messages (right-aligned), suggestion bubbles (highlighted), and error messages (red). New messages SHALL auto-scroll to the bottom.

#### Scenario: Agent reply appears as left bubble
- **WHEN** the UI Agent produces a text reply
- **THEN** a left-aligned bubble labeled "Agent" appears with the reply text

#### Scenario: Suggestion bubble with accept/ignore actions
- **WHEN** a `suggestion_final` WebSocket event is received
- **THEN** a highlighted bubble appears in the chat area with "采用" and "忽略" action buttons

#### Scenario: Accept suggestion inserts interviewer bubble
- **WHEN** user clicks "采用" on a suggestion bubble
- **THEN** a right-aligned "面试官" bubble with the suggestion text is inserted into the chat (no backend call is made)

#### Scenario: Error message shown as red bubble
- **WHEN** an `error` WebSocket event is received
- **THEN** a red-styled bubble appears in the chat area with the error message

### Requirement: Streaming suggestion rendering
The system SHALL render suggestion content progressively as `suggestion_delta` events arrive, updating the current bubble in place without creating new bubbles for each delta.

#### Scenario: Streaming delta updates existing bubble
- **WHEN** multiple `suggestion_delta` events arrive for the same suggestion
- **THEN** the bubble content grows incrementally without flicker or duplication

#### Scenario: Suggestion finalized on suggestion_final
- **WHEN** a `suggestion_final` event is received
- **THEN** the streaming bubble is marked complete and "采用"/"忽略" buttons appear

### Requirement: File upload via PDF button
The system SHALL provide an upload button in the input area that opens a file picker limited to PDF files. After selection the file SHALL be sent to `POST /api/resume/upload` and the Agent SHALL reflect the result in the chat.

#### Scenario: PDF selected and uploaded
- **WHEN** user selects a PDF file via the upload button
- **THEN** the file is sent to `POST /api/resume/upload` and a progress indicator appears

#### Scenario: Upload result shown in chat
- **WHEN** the upload API returns successfully
- **THEN** an Agent bubble shows the parsed candidate name, skills, and generated question list

### Requirement: Right panel with three tabs
The system SHALL provide a right-side panel with tabs: "转写" (transcript), "题目" (question plan), and "报告" (eval report).

#### Scenario: Transcript tab updates in real time
- **WHEN** a `transcript` WebSocket event is received
- **THEN** the transcript tab appends the new segment (source label + text)

#### Scenario: Question tab shows question_plan
- **WHEN** a `session_snapshot` event is received containing `question_plan`
- **THEN** the question tab renders the list with checkboxes for each question

#### Scenario: Report tab activates after interview stops
- **WHEN** user stops the interview and `GET /api/interview/eval` returns a report
- **THEN** the report tab becomes active and displays dimension scores, overall score, and recommendation

### Requirement: Manual transcript input in transcript tab
The system SHALL provide a manual input field in the transcript tab with a source selector (候选人 / 面试官). Submitting SHALL send a `manual_input` WebSocket message to the server.

#### Scenario: Manual input sent as WebSocket message
- **WHEN** user types text, selects source, and clicks send in the transcript tab
- **THEN** a `{ type: "manual_input", source: "<source>", text: "<text>" }` message is sent over the WebSocket

### Requirement: Inline transcript entries in chat area
The system SHALL optionally display lightweight transcript entries inline in the main chat area when real-time transcripts arrive.

#### Scenario: Transcript entry inline display
- **WHEN** a `transcript` event is received and inline display is enabled
- **THEN** a compact, dimmed entry (source + text) appears in the chat flow below the latest message
