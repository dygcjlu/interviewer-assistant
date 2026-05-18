## ADDED Requirements

### Requirement: CandidateProfile includes age and resume_markdown_path fields
The system SHALL add `age: int | None = None` and `resume_markdown_path: str | None = None` to the `CandidateProfile` model in `src/models/candidate.py`. Both fields SHALL default to `None` for backward compatibility with existing records.

#### Scenario: New fields present in CandidateProfile
- **WHEN** a `CandidateProfile` object is instantiated without providing `age` or `resume_markdown_path`
- **THEN** both fields default to `None` without raising an error

#### Scenario: age populated from resume parsing
- **WHEN** the LLM parses a resume containing age information
- **THEN** `candidate.age` is set to the extracted integer value

#### Scenario: Existing DB records remain valid
- **WHEN** the database is initialized with the updated schema after migration
- **THEN** rows created before migration have `age = NULL` and `resume_markdown_path = NULL` without data loss

### Requirement: DB schema migration for new fields
The system SHALL apply `ALTER TABLE candidates ADD COLUMN age INTEGER` and `ALTER TABLE candidates ADD COLUMN resume_markdown_path TEXT` migrations idempotently in `database.py`'s `initialize()` method.

#### Scenario: Migration runs on fresh database
- **WHEN** `database.initialize()` is called on a new SQLite database
- **THEN** the `candidates` table is created with `age` and `resume_markdown_path` columns

#### Scenario: Migration is idempotent on existing database
- **WHEN** `database.initialize()` is called on a database where `age` already exists
- **THEN** no error is raised and no duplicate column is created
