import json
from pathlib import Path

from migrator import (
    MigrationOrchestrator,
    build_history_comment,
    build_issue_mapping,
    build_subtask_payload,
    load_mapping_file,
)


def test_load_mapping_file_reads_json(tmp_path: Path) -> None:
    path = tmp_path / "mapping.json"
    path.write_text(json.dumps({"ABC-1": "XYZ-1"}), encoding="utf-8")
    assert load_mapping_file(path) == {"ABC-1": "XYZ-1"}


def test_build_history_comment_contains_change_details() -> None:
    issue = {
        "changelog": {
            "histories": [
                {"created": "2024-01-01", "items": [{"field": "status", "fromString": "Open", "toString": "Done"}]}
            ]
        }
    }
    comment = build_history_comment(issue)
    assert "status" in comment
    assert "Open" in comment
    assert "Done" in comment


def test_build_subtask_payload_uses_parent_mapping() -> None:
    payload = build_subtask_payload(
        source_issue={"key": "ABC-2", "fields": {"summary": "Subtask", "description": "Hello"}},
        parent_target_key="XYZ-1",
    )
    assert payload["fields"]["summary"] == "Subtask"
    assert payload["fields"]["parent"]["key"] == "XYZ-1"


def test_build_subtask_payload_uses_target_project() -> None:
    payload = build_subtask_payload(
        source_issue={"key": "ABC-2", "fields": {"summary": "Subtask", "description": "Hello"}},
        parent_target_key="XYZ-1",
        target_project_key="MIG",
    )
    assert payload["fields"]["project"]["key"] == "MIG"


def test_build_issue_mapping_matches_by_summary_and_type() -> None:
    source_issues = [
        {"key": "ABC-1", "fields": {"summary": "Login bug", "issuetype": {"name": "Bug"}}},
    ]
    target_issues = [
        {"key": "XYZ-1", "fields": {"summary": "Login bug", "issuetype": {"name": "Bug"}}},
    ]

    mapping = build_issue_mapping(source_issues, target_issues)

    assert mapping == {"ABC-1": "XYZ-1"}


def test_headers_use_basic_auth_when_configured() -> None:
    orchestrator = MigrationOrchestrator({"target": {"basic_auth": ["user@example.com", "token"]}})

    headers = orchestrator._headers()

    assert headers["Authorization"].startswith("Basic ")


def test_build_url_uses_api_version_2_by_default() -> None:
    orchestrator = MigrationOrchestrator({"source": {"server": "https://example.com"}, "target": {"server": "https://example.com"}})

    assert orchestrator._build_url("/search") == "https://example.com/rest/api/2/search"


def test_build_search_url_uses_project_key_when_provided() -> None:
    orchestrator = MigrationOrchestrator({"source": {"project_key": "TEST"}, "target": {"project_key": "TST"}})

    url = orchestrator._build_search_url(orchestrator.source)

    assert "fields=summary%2Cproject%2Ckey" in url
    assert "jql=project%3DTEST" in url


def test_headers_use_bearer_auth_when_configured() -> None:
    orchestrator = MigrationOrchestrator({"target": {"bearer_auth": "abc123"}})

    headers = orchestrator._headers()

    assert headers["Authorization"] == "Bearer abc123"


def test_fetch_source_issues_uses_source_server_and_fields(monkeypatch) -> None:
    orchestrator = MigrationOrchestrator(
        {
            "source": {
                "server": "https://source.example.com",
                "project": "ABC",
            },
            "target": {"server": "https://target.example.com"},
            "source_fields": ["summary", "project", "key"],
        }
    )
    captured: dict[str, str] = {}

    def fake_get_json(url: str, auth_config=None) -> dict[str, list[dict[str, str]]]:
        captured["url"] = url
        captured["auth_config"] = auth_config
        return {"issues": [{"key": "ABC-1"}]}

    monkeypatch.setattr(orchestrator, "_get_json", fake_get_json)

    issues = orchestrator.fetch_source_issues()

    assert issues == [{"key": "ABC-1"}]
    assert captured["url"].startswith("https://source.example.com/rest/api/2/search")
    assert "fields=summary%2Cproject%2Ckey" in captured["url"]
    assert "jql=project%3DABC" in captured["url"]
    assert captured["auth_config"] == orchestrator.source


def test_create_linked_issues_creates_missing_linked_issues(monkeypatch) -> None:
    orchestrator = MigrationOrchestrator(
        {
            "source": {"server": "https://source.example.com", "project": "ABC"},
            "target": {"server": "https://target.example.com", "project": "NTS"},
        }
    )
    orchestrator.mapping = {}
    source_issues = [
        {
            "key": "ABC-1",
            "fields": {
                "summary": "Parent issue",
                "issuetype": {"name": "Task"},
                "issuelinks": [{"inwardIssue": {"key": "ABC-2"}}],
            },
        },
        {"key": "ABC-2", "fields": {"summary": "Child issue", "issuetype": {"name": "Task"}}},
    ]
    created_payloads: list[dict[str, object]] = []

    def fake_create_target_issue(payload: dict[str, object]) -> str:
        created_payloads.append(payload)
        return "NTS-2"

    monkeypatch.setattr(orchestrator, "_create_target_issue", fake_create_target_issue)
    monkeypatch.setattr(orchestrator, "_write_mapping", lambda: None)

    orchestrator.create_linked_issues(source_issues)

    assert orchestrator.mapping == {"ABC-2": "NTS-2"}
    assert created_payloads[0]["fields"]["summary"] == "Child issue"
