#!/usr/bin/env python3
"""Fetch a GitHub account's repositories and update this profile README."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parent
CONFIG_PATH = ROOT / ".project-index.json"
README_PATH = ROOT / "README.md"
BEIJING_TIME = ZoneInfo("Asia/Shanghai")
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
    profile_token = os.environ.get("PROFILE_TOKEN", "")
    token = profile_token or os.environ.get("GITHUB_TOKEN", "")
    if config.get("include_private", False) and not profile_token:
        raise SystemExit(
            "已启用私有项目，但缺少 PROFILE_TOKEN。"
            "请在 GitHub Actions Secrets 中配置后重新运行工作流；"
            "为避免生成不完整列表，本次未更新 README。"
        )
    # A personal token can include private repositories when explicitly enabled.
    # GitHub Actions' built-in token is repository-scoped, so public indexes must
    # keep using the account endpoint even when that token is present.
    if profile_token and config.get("include_private", False):
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
    if include_private and not any(repo.get("private") for repo in filtered):
        raise SystemExit(
            "已启用私有项目，但 PROFILE_TOKEN 未返回任何私有仓库。"
            "请确认 Fine-grained token 的 Resource owner 为当前账号，"
            "Repository access 为 All repositories；为避免清空私有项目，本次未更新 README。"
        )
    return sorted(filtered, key=lambda repo: repo.get("pushed_at") or "", reverse=True)


def escape(value: object) -> str:
    return str(value or "").strip().replace("|", "\\|").replace("\n", " ")


def render(repositories: list[dict]) -> str:
    updated = datetime.now(BEIJING_TIME).strftime("%Y-%m-%d %H:%M CST")
    lines = [
        f"_自动收集 {len(repositories)} 个 GitHub 项目；最后更新：{updated}_",
        "",
        "| 项目 | 技术 |",
        "| --- | --- |",
    ]
    for repo in repositories:
        badges = []
        is_private = bool(repo.get("private"))
        if is_private:
            lines.append(f"| 🔒 {escape(repo['name'])} | — |")
            continue
        if repo.get("fork"):
            badges.append("Fork")
        if repo.get("archived"):
            badges.append("Archived")
        suffix = f" ({', '.join(badges)})" if badges else ""
        name = escape(repo["name"]) + suffix
        link = repo["html_url"]
        language = escape(repo.get("language")) or "—"
        lines.append(f"| [{name}]({link}) | {language} |")
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
    action = parser.add_mutually_exclusive_group()
    action.add_argument(
        "--commit",
        "--sync",
        dest="commit",
        action="store_true",
        help="更新并提交 README（--sync 是兼容别名）",
    )
    action.add_argument("--push", action="store_true", help="更新、提交并推送")
    action.add_argument(
        "--upload",
        action="store_true",
        help="直接提交并推送现有 README，不重新收集数据",
    )
    args = parser.parse_args()
    if args.upload:
        sync(push=True)
        print("已提交并上传当前 README")
        return
    config = load_config()
    repositories = fetch_repositories(config)
    update_readme(repositories)
    if args.commit or args.push:
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
