#!/usr/bin/env python3
"""Fetch a GitHub account's repositories and update this profile README."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parent
CONFIG_PATH = ROOT / ".project-index.json"
README_PATH = ROOT / "README.md"
START = "<!-- PROJECTS:START -->"
END = "<!-- PROJECTS:END -->"


def git(*args: str, check: bool = False) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(ROOT), *args],
        text=True,
        capture_output=True,
        check=check,
    )


def load_config() -> dict:
    config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    if not config.get("github_username"):
        raise SystemExit(".project-index.json 缺少 github_username")
    return config


def api_get(url: str, token: str) -> list[dict]:
    request = Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "yamakawanin-project-index",
            **({"Authorization": f"Bearer {token}"} if token else {}),
        },
    )
    with urlopen(request, timeout=20) as response:
        return json.load(response)


def fetch_repositories(config: dict) -> list[dict]:
    username = config["github_username"]
    token = os.environ.get("GITHUB_TOKEN", "")
    # The authenticated endpoint can include private repositories. They are hidden
    # from a public README unless explicitly enabled in the config.
    if token:
        base = "https://api.github.com/user/repos?affiliation=owner"
    else:
        base = f"https://api.github.com/users/{username}/repos?"

    repositories: list[dict] = []
    page = 1
    while True:
        separator = "&" if "?" in base else "?"
        batch = api_get(
            f"{base}{separator}per_page=100&page={page}&sort=updated&direction=desc",
            token,
        )
        if not isinstance(batch, list):
            raise SystemExit("GitHub API 返回了非预期数据")
        repositories.extend(batch)
        if len(batch) < 100:
            break
        page += 1

    include_forks = bool(config.get("include_forks", True))
    include_archived = bool(config.get("include_archived", True))
    include_private = bool(config.get("include_private", False))
    filtered = [
        repo
        for repo in repositories
        if repo.get("owner", {}).get("login", "").casefold() == username.casefold()
        and (include_forks or not repo.get("fork"))
        and (include_archived or not repo.get("archived"))
        and (include_private or not repo.get("private"))
    ]
    return sorted(filtered, key=lambda repo: repo.get("pushed_at") or "", reverse=True)


def escape(value: object) -> str:
    return str(value or "").strip().replace("|", "\\|").replace("\n", " ")


def render(repositories: list[dict]) -> str:
    updated = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M %Z")
    lines = [
        f"_自动收集 {len(repositories)} 个 GitHub 项目；最后更新：{updated}_",
        "",
        "| 项目 | 技术 | 简介 |",
        "| --- | --- | --- |",
    ]
    for repo in repositories:
        badges = []
        if repo.get("fork"):
            badges.append("Fork")
        if repo.get("archived"):
            badges.append("Archived")
        suffix = f" ({', '.join(badges)})" if badges else ""
        name = escape(repo["name"]) + suffix
        link = repo["html_url"]
        language = escape(repo.get("language")) or "—"
        description = escape(repo.get("description")) or "暂无简介"
        lines.append(f"| [{name}]({link}) | {language} | {description} |")
    return "\n".join(lines)


def update_readme(repositories: list[dict]) -> None:
    original = README_PATH.read_text(encoding="utf-8")
    if original.count(START) != 1 or original.count(END) != 1:
        raise SystemExit("README 自动更新标记异常，已拒绝覆盖。")
    before, rest = original.split(START, 1)
    _generated, after = rest.split(END, 1)
    README_PATH.write_text(
        f"{before}{START}\n{render(repositories)}\n{END}{after}",
        encoding="utf-8",
    )


def sync(push: bool) -> None:
    top = git("rev-parse", "--show-toplevel")
    if top.returncode != 0 or Path(top.stdout.strip()).resolve() != ROOT:
        raise SystemExit("安全检查失败：当前目录不是独立 Git 仓库，已停止同步。")
    git("add", "--", "README.md", check=True)
    changed = git("diff", "--cached", "--quiet", "--", "README.md")
    if changed.returncode == 1:
        git("commit", "-m", "docs: update GitHub project index", check=True)
    if push:
        if git("remote", "get-url", "origin").returncode != 0:
            raise SystemExit("未配置 origin，无法推送。")
        git("push", "-u", "origin", "HEAD", check=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="自动收集 GitHub 项目并更新 README")
    parser.add_argument("--sync", action="store_true", help="更新并提交 README")
    parser.add_argument("--push", action="store_true", help="更新、提交并推送")
    args = parser.parse_args()
    repositories = fetch_repositories(load_config())
    update_readme(repositories)
    if args.sync or args.push:
        sync(args.push)
    print(f"已从 GitHub 收集 {len(repositories)} 个项目并更新 README")


if __name__ == "__main__":
    try:
        main()
    except (HTTPError, URLError, TimeoutError) as error:
        print(f"访问 GitHub API 失败：{error}", file=sys.stderr)
        raise SystemExit(1)
    except subprocess.CalledProcessError as error:
        print(error.stderr.strip() or str(error), file=sys.stderr)
        raise SystemExit(error.returncode)
