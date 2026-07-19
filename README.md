# Jira phased migration helper

This project is a lightweight second-phase migration utility for Jira.

It assumes the initial bulk import of main issues has already been done manually in the target Jira project and that a source-to-target mapping already exists.

The tool can then:

- create subtasks under already-created parent issues
- create issue links between mapped issues
- add comments from the source issue to the target issue
- copy a simple changelog/history summary as a comment
- re-run only a selected set of issues for reconciliation

## Quick start

1. Copy config/example_config.json to config/config.json and fill in your source/target Jira details.
2. Create a source-to-target mapping file as JSON:

```json
{
  "ABC-101": "XYZ-101",
  "ABC-102": "XYZ-102"
}
```

3. Run:

```bash
python main.py --config config/config.json
```

## Supported phases

- Phase 2: subtasks and links
- Phase 3: comments and history
- Phase 4: reconciliation
