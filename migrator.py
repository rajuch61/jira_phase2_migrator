import csv
import json
import logging
import os
import ssl
from pathlib import Path
from typing import Any
from urllib import error, request
from urllib.parse import quote


def build_issue_mapping(source_issues: list[dict[str, Any]], target_issues: list[dict[str, Any]]) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for source_issue in source_issues:
        source_key = source_issue.get("key")
        if not source_key:
            continue
        source_fields = source_issue.get("fields") or {}
        source_summary = str(source_fields.get("summary") or "").strip().lower()
        source_type = str((source_fields.get("issuetype") or {}).get("name") or "").strip().lower()
        source_labels = sorted(str(label).strip().lower() for label in (source_fields.get("labels") or []))

        for target_issue in target_issues:
            target_key = target_issue.get("key")
            if not target_key or target_key in mapping.values():
                continue
            target_fields = target_issue.get("fields") or {}
            target_summary = str(target_fields.get("summary") or "").strip().lower()
            target_type = str((target_fields.get("issuetype") or {}).get("name") or "").strip().lower()
            target_labels = sorted(str(label).strip().lower() for label in (target_fields.get("labels") or []))

            summary_matches = source_summary and source_summary == target_summary
            type_matches = source_type and source_type == target_type
            labels_match = source_labels and source_labels == target_labels

            if summary_matches and (type_matches or labels_match):
                mapping[source_key] = target_key
                break
    return mapping


class MigrationOrchestrator:
    def __init__(self, config: dict[str, Any]) -> None:
        self.config = config
        self.source = config.get("source", {})
        self.target = config.get("target", {})
        self.mapping_file = Path(config.get("mapping_file", "mapping.json"))
        self.reconcile_file = Path(config.get("reconcile_file", "reconcile.csv"))
        self.log_file = Path(config.get("log_file", "logs/migration.log"))
        self.log_file.parent.mkdir(parents=True, exist_ok=True)
        self.logger = self._build_logger()
        self.mapping = self._load_mapping()

    def _build_logger(self) -> logging.Logger:
        logger = logging.getLogger("jira_phase2_migrator")
        logger.setLevel(logging.INFO)
        logger.handlers.clear()
        handler = logging.FileHandler(self.log_file, encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        logger.addHandler(handler)
        return logger

    def run_all(self) -> None:
        self.logger.info("Starting phased migration")
        source_issues = self.fetch_source_issues()
        target_issues = self.fetch_target_issues()
        self.discover_mapping(source_issues, target_issues)
        self.create_linked_issues(source_issues)
        self.create_subtasks(source_issues)
        self.create_links(source_issues)
        self.create_comments(source_issues)
        self.create_history_comments(source_issues)
        self.write_reconciliation_report(source_issues)
        self.logger.info("Finished phased migration")

    def _load_mapping(self) -> dict[str, str]:
        if "mapping_file" in self.config:
            return load_mapping_file(self.mapping_file)
        return {}

    def discover_mapping(self, source_issues: list[dict[str, Any]], target_issues: list[dict[str, Any]]) -> dict[str, str]:
        discovered = build_issue_mapping(source_issues, target_issues)
        if discovered:
            self.mapping.update(discovered)
            self._write_mapping()
        return discovered

    def fetch_source_issues(self) -> list[dict[str, Any]]:
        url = self._build_search_url(self.source)
        payload = self._get_json(url, self.source)
        issues = payload.get("issues") if isinstance(payload, dict) else []
        return list(issues)

    def fetch_target_issues(self) -> list[dict[str, Any]]:
        url = self._build_search_url(self.target)
        payload = self._get_json(url, self.target)
        issues = payload.get("issues") if isinstance(payload, dict) else []
        return list(issues)

    def create_linked_issues(self, issues: list[dict[str, Any]]) -> None:
        for issue in issues:
            for link in issue.get("fields", {}).get("issuelinks", []) or []:
                linked_key = link.get("inwardIssue", {}).get("key") or link.get("outwardIssue", {}).get("key")
                if not linked_key or linked_key in self.mapping:
                    continue
                linked_issue = next((item for item in issues if item.get("key") == linked_key), None)
                if linked_issue is None:
                    continue
                target_key = self.mapping.get(linked_key)
                if target_key:
                    continue
                payload = build_issue_payload(linked_issue, self.target.get("project", "NTS"))
                created_key = self._create_target_issue(payload)
                if created_key:
                    self.mapping[linked_key] = created_key
                    self._write_mapping()

    def create_subtasks(self, issues: list[dict[str, Any]]) -> None:
        for issue in issues:
            if not self._is_subtask(issue):
                continue
            parent_key = issue.get("fields", {}).get("parent", {}).get("key")
            if not parent_key:
                continue
            parent_target = self.mapping.get(parent_key)
            if not parent_target:
                self.logger.warning("Parent target not found for %s", parent_key)
                continue
            target_payload = build_subtask_payload(
                issue,
                parent_target,
                self.target.get("project", "NTS"),
            )
            target_key = self._create_target_issue(target_payload)
            if target_key:
                self.mapping[issue.get("key")] = target_key
                self._write_mapping()

    def create_links(self, issues: list[dict[str, Any]]) -> None:
        for issue in issues:
            source_key = issue.get("key")
            target_key = self.mapping.get(source_key)
            if not target_key:
                continue
            for link in issue.get("fields", {}).get("issuelinks", []) or []:
                linked_key = link.get("inwardIssue", {}).get("key") or link.get("outwardIssue", {}).get("key")
                linked_target = self.mapping.get(linked_key)
                if not linked_target:
                    continue
                self._create_issue_link(target_key, linked_target, link)

    def create_comments(self, issues: list[dict[str, Any]]) -> None:
        for issue in issues:
            source_key = issue.get("key")
            target_key = self.mapping.get(source_key)
            if not target_key:
                continue
            comments = issue.get("fields", {}).get("comment", {}).get("comments", []) or []
            for comment in comments:
                body = comment.get("body")
                if isinstance(body, dict):
                    body = json.dumps(body)
                if body:
                    self._add_comment(target_key, str(body))

    def create_history_comments(self, issues: list[dict[str, Any]]) -> None:
        for issue in issues:
            source_key = issue.get("key")
            target_key = self.mapping.get(source_key)
            if not target_key:
                continue
            history_text = build_history_comment(issue)
            if history_text:
                self._add_comment(target_key, history_text)

    def write_reconciliation_report(self, issues: list[dict[str, Any]]) -> None:
        rows = []
        for issue in issues:
            source_key = issue.get("key")
            target_key = self.mapping.get(source_key)
            rows.append({"source_key": source_key, "target_key": target_key or ""})
        path = self.reconcile_file
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=["source_key", "target_key"])
            writer.writeheader()
            writer.writerows(rows)

    def _create_target_issue(self, payload: dict[str, Any]) -> str | None:
        url = self._build_url("/issue", api_version=self._get_api_version(self.target))
        response = self._post_json(url, payload)
        return response.get("key") if isinstance(response, dict) else None

    def _create_issue_link(self, source_key: str, target_key: str, link: dict[str, Any]) -> None:
        payload = {
            "type": {"name": "Relates"},
            "inwardIssue": {"key": source_key},
            "outwardIssue": {"key": target_key},
        }
        self._post_json(self._build_url("/issueLink", api_version=self._get_api_version(self.target)), payload)

    def _add_comment(self, issue_key: str, body: str) -> None:
        payload = {"body": body}
        self._post_json(self._build_url(f"/issue/{issue_key}/comment", api_version=self._get_api_version(self.target)), payload)

    def _build_url(self, path: str, server: str | None = None, api_version: str | None = None) -> str:
        base = (server or self.target.get("server", "")).rstrip("/")
        version = str(api_version or self._get_api_version())
        return f"{base}/rest/api/{version}{path}"

    def _get_api_version(self, auth_config: dict[str, Any] | None = None) -> str:
        config = auth_config or self.target
        version = config.get("api_version") or self.config.get("api_version") or "2"
        return str(version)

    def _build_search_url(self, config: dict[str, Any]) -> str:
        base_url = self._build_url("/search", config.get("server", ""), self._get_api_version(config))
        fields = config.get("fields") or self.config.get("source_fields") or ["summary", "project", "key"]
        field_names = ",".join(str(field) for field in fields)
        query_params = [f"startAt=0", f"maxResults=100", f"fields={quote(field_names)}"]

        project_key = config.get("project_key") or config.get("project") or ""
        if project_key:
            query_params.append(f"jql=project%3D{quote(project_key)}")

        return f"{base_url}?{'&'.join(query_params)}"

    def _get_json(self, url: str, auth_config: dict[str, Any] | None = None) -> Any:
        config = auth_config or self.target
        headers = self._headers(config)
        print(f"Fetching Jira issues from: {url}")
        print(f"Headers: {headers}")
        print(f"Request URL: {url}")
        req = request.Request(url, headers=headers)
        try:
            with request.urlopen(req, timeout=config.get("timeout", 30), context=self._build_ssl_context(config)) as response:
                raw_body = response.read().decode("utf-8", errors="replace").strip()
                if not raw_body:
                    self.logger.warning("Received empty response body from %s", url)
                    return {}
                try:
                    return json.loads(raw_body)
                except json.JSONDecodeError as exc:
                    self.logger.error("Invalid JSON response from %s: %s", url, raw_body[:500])
                    raise exc
        except error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="ignore")
            self.logger.error("HTTP error %s for %s: %s", exc.code, url, body)
            if exc.code in {410, 404}:
                fallback_url = url.replace("/rest/api/3/", "/rest/api/2/")
                self.logger.error("Retrying with fallback URL: %s", fallback_url)
                try:
                    fallback_req = request.Request(fallback_url, headers=headers)
                    with request.urlopen(fallback_req, timeout=config.get("timeout", 30), context=self._build_ssl_context(config)) as fallback_response:
                        raw_body = fallback_response.read().decode("utf-8", errors="replace").strip()
                        if not raw_body:
                            self.logger.warning("Received empty response body from %s", fallback_url)
                            return {}
                        return json.loads(raw_body)
                except error.HTTPError as fallback_exc:
                    fallback_body = fallback_exc.read().decode("utf-8", errors="ignore")
                    self.logger.error("Fallback HTTP error %s for %s: %s", fallback_exc.code, fallback_url, fallback_body)
            raise
        except (error.URLError, ssl.SSLError, TimeoutError) as exc:
            self.logger.error("Unable to reach Jira at %s: %s", url, exc)
            raise RuntimeError(f"Unable to reach Jira at {url}: {exc}") from exc

    def _post_json(self, url: str, payload: dict[str, Any], auth_config: dict[str, Any] | None = None) -> Any:
        config = auth_config or self.target
        data = json.dumps(payload).encode("utf-8")
        headers = self._headers(config)
        print(f"Posting to Jira URL: {url}")
        print(f"Request URL: {url}")
        print(f"Payload: {payload}")
        print(f"Headers: {headers}")
        req = request.Request(url, data=data, headers=headers, method="POST")
        try:
            with request.urlopen(req, timeout=config.get("timeout", 30), context=self._build_ssl_context(config)) as response:
                raw_body = response.read().decode("utf-8", errors="replace").strip()
                if not raw_body:
                    self.logger.warning("Received empty response body from %s", url)
                    return {}
                try:
                    return json.loads(raw_body)
                except json.JSONDecodeError as exc:
                    self.logger.error("Invalid JSON response from %s: %s", url, raw_body[:500])
                    raise exc
        except error.HTTPError as exc:
            self.logger.error("HTTP error %s for %s: %s", exc.code, url, exc.read().decode("utf-8", errors="ignore"))
            return {}
        except (error.URLError, ssl.SSLError, TimeoutError) as exc:
            self.logger.error("Unable to reach Jira at %s: %s", url, exc)
            return {}

    def _build_ssl_context(self, auth_config: dict[str, Any] | None = None) -> ssl.SSLContext | None:
        config = auth_config or self.target
        verify_ssl = config.get("verify_ssl", True)
        if verify_ssl:
            return None

        context = ssl.create_default_context()
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
        return context

    def _headers(self, auth_config: dict[str, Any] | None = None) -> dict[str, str]:
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        config = auth_config or self.target

        basic_auth = config.get("basic_auth") or []
        if len(basic_auth) == 2:
            pair = f"{basic_auth[0]}:{basic_auth[1]}".encode("utf-8")
            headers["Authorization"] = "Basic " + pair.decode("latin1")
            return headers

        bearer_auth = config.get("bearer_auth")
        if bearer_auth:
            headers["Authorization"] = f"Bearer {bearer_auth}"
            return headers

        return headers

    def _write_mapping(self) -> None:
        self.mapping_file.parent.mkdir(parents=True, exist_ok=True)
        self.mapping_file.write_text(json.dumps(self.mapping, indent=2), encoding="utf-8")

    def _is_subtask(self, issue: dict[str, Any]) -> bool:
        fields = issue.get("fields") or {}
        issuetype = fields.get("issuetype") or {}
        name = issuetype.get("name") or ""
        return name.lower() in {"sub-task", "subtask"}


def load_mapping_file(path: str | os.PathLike[str] | None) -> dict[str, str]:
    if not path:
        return {}
    mapping_path = Path(path)
    if not mapping_path.exists():
        return {}
    return json.loads(mapping_path.read_text(encoding="utf-8"))


def build_issue_payload(source_issue: dict[str, Any], target_project_key: str | None = None) -> dict[str, Any]:
    fields = source_issue.get("fields") or {}
    project_key = target_project_key or "NTS"
    return {
        "fields": {
            "project": {"key": project_key},
            "summary": fields.get("summary", ""),
            "description": fields.get("description", ""),
            "issuetype": fields.get("issuetype", {"name": "Task"}),
        }
    }


def build_subtask_payload(
    source_issue: dict[str, Any],
    parent_target_key: str,
    target_project_key: str | None = None,
) -> dict[str, Any]:
    fields = source_issue.get("fields") or {}
    project_key = target_project_key or "NTS"
    return {
        "fields": {
            "project": {"key": project_key},
            "summary": fields.get("summary", ""),
            "description": fields.get("description", ""),
            "issuetype": {"name": "Sub-task"},
            "parent": {"key": parent_target_key},
        }
    }


def build_history_comment(issue: dict[str, Any]) -> str:
    histories = (issue.get("changelog") or {}).get("histories") or []
    if not histories:
        return ""
    chunks = []
    for history in histories:
        created = history.get("created") or ""
        for item in history.get("items") or []:
            field = item.get("field") or ""
            from_value = item.get("fromString") or ""
            to_value = item.get("toString") or ""
            if field:
                chunks.append(f"{created}: {field}: {from_value} -> {to_value}")
    return "History:\n" + "\n".join(chunks) if chunks else ""
