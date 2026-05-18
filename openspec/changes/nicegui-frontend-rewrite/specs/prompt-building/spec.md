## ADDED Requirements

### Requirement: CandidateProfile summary injected into system prompt fixed section
The system SHALL inject key `CandidateProfile` fields (name, age, education, skills, resume_summary) into the system prompt's fixed section in `PromptBuilder`. This injection SHALL occur when a candidate is associated with the current session and SHALL be available to both `InterviewAgent` and `EvalAgent` without requiring them to re-parse the resume.

#### Scenario: System prompt includes candidate summary when candidate is set
- **WHEN** `PromptBuilder.build_system_prompt()` is called and the session has a `CandidateProfile`
- **THEN** the fixed section contains a structured block with candidate name, age, education list, skills list, and resume_summary

#### Scenario: System prompt omits candidate block when no candidate
- **WHEN** `PromptBuilder.build_system_prompt()` is called and the session has no `CandidateProfile`
- **THEN** the fixed section does not include a candidate summary block

#### Scenario: InterviewAgent receives candidate context without extra parsing
- **WHEN** `InterviewAgent` generates a suggestion for the current round
- **THEN** the system prompt already contains the candidate summary and no additional resume parsing tool call is needed for basic context
