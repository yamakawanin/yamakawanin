#!/usr/bin/env python3
"""Aggregate GitHub language byte counts for this profile's public projects."""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
OUTPUT_PATH = ROOT / "generated" / "languages.md"
API_ROOT = "https://api.github.com"


class GitHubApiError(RuntimeError):
    pass


def api_get(url: str, token: str) -> Any:
    request = Request(url, headers={"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "yamakawanin-language-analysis", **({"Authorization": f"Bearer {token}"} if token else {})})
    try:
        with urlopen(request, timeout=30) as response:
            return json.load(response)
    except HTTPError as error:
        raise GitHubApiError(f"GitHub API request failed ({error.code}) for {url}; rate-limit remaining: {error.headers.get('X-RateLimit-Remaining', 'unknown')}.") from error
    except (URLError, TimeoutError) as error:
        raise GitHubApiError(f"GitHub API request failed for {url}: {error}") from error


def fetch_public_repositories(username: str, request: Callable[[str], Any], profile_repository: str) -> list[dict[str, Any]]:
    repositories: list[dict[str, Any]] = []
    page = 1
    while True:
        batch = request(f"{API_ROOT}/users/{quote(username, safe='')}/repos?type=owner&per_page=100&page={page}&sort=full_name&direction=asc")
        if not isinstance(batch, list):
            raise GitHubApiError("GitHub repositories endpoint returned unexpected data.")
        repositories.extend(batch)
        if len(batch) < 100:
            break
        page += 1
    return [repo for repo in repositories if repo.get("owner", {}).get("login", "").casefold() == username.casefold() and not repo.get("private", False) and not repo.get("fork", False) and not repo.get("archived", False) and repo.get("name", "").casefold() != profile_repository.casefold()]


def fetch_languages(username: str, repository: str, request: Callable[[str], Any]) -> dict[str, int]:
    data = request(f"{API_ROOT}/repos/{quote(username, safe='')}/{quote(repository, safe='')}/languages")
    if not isinstance(data, dict) or not all(isinstance(language, str) and isinstance(bytes_used, int) for language, bytes_used in data.items()):
        raise GitHubApiError(f"Languages endpoint returned unexpected data for {repository!r}.")
    return dict(data)


def escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace("|", "\\|").replace("\n", " ")


def render_section(projects: list[dict[str, Any]]) -> str:
    totals: dict[str, int] = {}
    for project in projects:
        for language, bytes_used in project["languages"].items():
            totals[language] = totals.get(language, 0) + bytes_used
    total_bytes = sum(totals.values())
    overall = sorted(totals.items(), key=lambda item: (-item[1], item[0].casefold()))
    lines = ["## 语言统计", "", f"_生成日期：{datetime.now(timezone.utc).date().isoformat()}（仅统计公开、非 Fork、非归档项目；不含本 Profile 仓库）。_", "", "| 语言 | 占比 | 检测字节数 |", "| --- | ---: | ---: |"]
    lines.extend(f"| {escape(language)} | {bytes_used / total_bytes * 100:.1f}% | {bytes_used:,} |" for language, bytes_used in overall) if overall else lines.append("| — | — | 0 |")
    lines.extend(["", "| 项目 | 主要语言 | 语言构成 |", "| --- | --- | --- |"])
    for project in sorted(projects, key=lambda item: item["name"].casefold()):
        languages = sorted(project["languages"].items(), key=lambda item: (-item[1], item[0].casefold()))
        project_total = sum(bytes_used for _language, bytes_used in languages)
        breakdown = ", ".join(f"{escape(language)} {bytes_used / project_total * 100:.0f}%" for language, bytes_used in languages) if languages else "未检测到编程语言"
        main = escape(languages[0][0]) if languages else "—"
        url = f"https://github.com/{quote(project['owner'], safe='')}/{quote(project['name'], safe='')}"
        lines.append(f"| [{escape(project['name'])}]({url}) | {main} | {breakdown} |")
    lines.extend(["", "> GitHub 语言统计以检测到的代码字节数为依据，不代表熟练度或使用经验。"])
    return "\n".join(lines)


def update_generated_file(section: str) -> bool:
    generated = section + "\n"
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    changed = not OUTPUT_PATH.exists() or OUTPUT_PATH.read_text(encoding="utf-8") != generated
    OUTPUT_PATH.write_text(generated, encoding="utf-8")
    return changed


def run_self_test() -> None:
    pages = {1: [{"name": "profile", "owner": {"login": "alice"}}, {"name": "private", "owner": {"login": "alice"}, "private": True}, {"name": "forked", "owner": {"login": "alice"}, "fork": True}, {"name": "archived", "owner": {"login": "alice"}, "archived": True}, {"name": "C++ tools", "owner": {"login": "alice"}}], 2: []}
    def request(url: str) -> Any:
        if "/repos?" in url:
            return pages[int(url.split("page=")[-1].split("&")[0])]
        if url.endswith("/languages"):
            return {"Python": 60, "C++": 40}
        raise AssertionError(url)
    repositories = fetch_public_repositories("alice", request, "profile")
    assert [repo["name"] for repo in repositories] == ["C++ tools"]
    section = render_section([{"name": "C++ tools", "owner": "alice", "languages": fetch_languages("alice", "C++ tools", request)}])
    assert "60.0%" in section and "40.0%" in section and "C%2B%2B%20tools" in section
    print("Self-test passed: pagination, filtering, encoding, and percentages.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Update profile language statistics from GitHub API data.")
    parser.add_argument("--username", default="yamakawanin")
    parser.add_argument("--profile-repository", default="yamakawanin")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        run_self_test()
        return
    token = os.environ.get("GITHUB_TOKEN", "")
    request = lambda url: api_get(url, token)
    repositories = fetch_public_repositories(args.username, request, args.profile_repository)
    projects = []
    for repository in repositories:
        name = repository.get("name")
        if not isinstance(name, str) or not name:
            raise GitHubApiError("A repository response did not include a valid name.")
        projects.append({"name": name, "owner": args.username, "languages": fetch_languages(args.username, name, request)})
    changed = update_generated_file(render_section(projects))
    print(f"Analyzed {len(projects)} repositories; {'updated' if changed else 'no content changes'}.")


if __name__ == "__main__":
    try:
        main()
    except (GitHubApiError, ValueError) as error:
        print(f"Language analysis failed: {error}", file=sys.stderr)
        raise SystemExit(1)
