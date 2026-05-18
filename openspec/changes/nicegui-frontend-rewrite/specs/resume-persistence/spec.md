## ADDED Requirements

### Requirement: PDF file persisted to resumes/ directory
The system SHALL save uploaded PDF files to `resumes/<session_id>_<timestamp>.pdf` instead of a temporary file. The `resumes/` directory SHALL be created on startup if it does not exist.

#### Scenario: PDF saved on upload
- **WHEN** `POST /api/resume/upload` receives a PDF file
- **THEN** the file is written to `resumes/<session_id>_<timestamp>.pdf` and the path is available for further processing

#### Scenario: Temporary file logic removed
- **WHEN** `POST /api/resume/upload` completes
- **THEN** no `tempfile.NamedTemporaryFile` is created and no `os.unlink` is called

### Requirement: Resume text saved as Markdown
The system SHALL convert `resume_text` to Markdown format and save it to `resumes/<session_id>.md` after PDF parsing. The absolute path SHALL be assigned to `candidate.resume_markdown_path`.

#### Scenario: Markdown file created on upload
- **WHEN** PDF parsing completes and `resume_text` is available
- **THEN** a Markdown file is written to `resumes/<session_id>.md`

#### Scenario: resume_markdown_path set on candidate
- **WHEN** the Markdown file is written successfully
- **THEN** `candidate.resume_markdown_path` contains the absolute path to the `.md` file

### Requirement: resumes/ directory gitignored
The system SHALL add `resumes/` to `.gitignore` (with a `.gitkeep` to preserve the directory) so uploaded files are not committed to the repository.

#### Scenario: resumes/ not tracked by git
- **WHEN** a PDF is uploaded and saved to `resumes/`
- **THEN** `git status` does not show the PDF or Markdown file as untracked
