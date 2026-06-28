#!/usr/bin/env python3
"""Fetch a GitHub account's repositories and update this profile README."""

from __future__ import annotations

import argparse
import html
import json
import os
import re
import subprocess
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parent
CONFIG_PATH = ROOT / ".project-index.json"
README_PATH = ROOT / "README.md"
CHART_PATH = ROOT / "assets" / "contributions.svg"
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
    token = os.environ.get("PROFILE_TOKEN") or os.environ.get("GITHUB_TOKEN", "")
    # A personal token can include private repositories when explicitly enabled.
    # GitHub Actions' built-in token is repository-scoped, so public indexes must
    # keep using the account endpoint even when that token is present.
    if token and config.get("include_private", False):
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


def fetch_contributions(username: str) -> list[tuple[date, int]]:
    """Fetch exact daily public contribution counts for the trailing 365 days."""
    today = date.today()
    first_day = today - timedelta(days=364)
    headers = {
        "Accept": "text/html",
        "User-Agent": "yamakawanin-project-index",
    }
    counts: dict[date, int] = {}
    for year in range(first_day.year, today.year + 1):
        url = (
            f"https://github.com/users/{username}/contributions"
            f"?from={year}-01-01&to={year}-12-31"
        )
        with urlopen(Request(url, headers=headers), timeout=20) as response:
            page = response.read().decode("utf-8")
        pattern = re.compile(
            r'data-date="(?P<date>\d{4}-\d{2}-\d{2})"[^>]*'
            r'id="(?P<id>[^"]+)"[^>]*>.*?'
            r'<tool-tip[^>]*for="(?P=id)"[^>]*>(?P<label>.*?)</tool-tip>',
            re.DOTALL,
        )
        for match in pattern.finditer(page):
            day = date.fromisoformat(match.group("date"))
            label = html.unescape(re.sub(r"<[^>]+>", "", match.group("label")))
            number = re.search(r"(\d+)\s+contribution", label)
            counts[day] = int(number.group(1)) if number else 0
    return [
        (first_day + timedelta(days=offset), counts.get(first_day + timedelta(days=offset), 0))
        for offset in range(365)
    ]


def write_contribution_chart(days: list[tuple[date, int]]) -> None:
    """Render weekly contribution totals as a self-contained SVG line chart."""
    weeks: list[tuple[date, int]] = []
    for index in range(0, len(days), 7):
        chunk = days[index : index + 7]
        weeks.append((chunk[-1][0], sum(count for _day, count in chunk)))

    width, height = 900, 280
    left, right, top, bottom = 58, 24, 32, 48
    plot_width = width - left - right
    plot_height = height - top - bottom
    maximum = max((count for _day, count in weeks), default=0)
    ceiling = max(5, ((maximum + 4) // 5) * 5)

    points = []
    for index, (_day, count) in enumerate(weeks):
        x = left + plot_width * index / max(1, len(weeks) - 1)
        y = top + plot_height * (1 - count / ceiling)
        points.append((x, y))
    point_text = " ".join(f"{x:.1f},{y:.1f}" for x, y in points)
    area = (
        f"M {points[0][0]:.1f} {top + plot_height} "
        + " ".join(f"L {x:.1f} {y:.1f}" for x, y in points)
        + f" L {points[-1][0]:.1f} {top + plot_height} Z"
    )

    grid = []
    for step in range(5):
        value = round(ceiling * step / 4)
        y = top + plot_height * (1 - step / 4)
        grid.append(
            f'<line x1="{left}" y1="{y:.1f}" x2="{width-right}" y2="{y:.1f}" '
            'class="grid"/>'
            f'<text x="{left-10}" y="{y+4:.1f}" text-anchor="end" class="label">{value}</text>'
        )

    month_labels = []
    previous_month = 0
    for index, (day, _count) in enumerate(weeks):
        if day.month != previous_month:
            x = left + plot_width * index / max(1, len(weeks) - 1)
            month_labels.append(
                f'<text x="{x:.1f}" y="{height-18}" text-anchor="middle" '
                f'class="label">{day.strftime("%b")}</text>'
            )
            previous_month = day.month

    total = sum(count for _day, count in days)
    svg = f"""<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}" role="img" aria-labelledby="title desc">
<title id="title">GitHub contributions over the last year</title>
<desc id="desc">{total} public contributions, grouped by week.</desc>
<style>
  .bg {{ fill: #ffffff; }} .grid {{ stroke: #d8dee4; stroke-width: 1; }}
  .label {{ fill: #57606a; font: 12px -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif; }}
  .title {{ fill: #24292f; font: 600 15px -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif; }}
  .area {{ fill: #2da44e; opacity: .14; }} .line {{ fill: none; stroke: #2da44e; stroke-width: 3; stroke-linejoin: round; stroke-linecap: round; }}
  @media (prefers-color-scheme: dark) {{
    .bg {{ fill: #0d1117; }} .grid {{ stroke: #30363d; }} .label {{ fill: #8b949e; }} .title {{ fill: #c9d1d9; }}
    .area {{ fill: #3fb950; }} .line {{ stroke: #3fb950; }}
  }}
</style>
<rect class="bg" width="100%" height="100%" rx="10"/>
<text x="{left}" y="21" class="title">最近一年：{total} 次公开贡献（按周）</text>
{''.join(grid)}
<path d="{area}" class="area"/>
<polyline points="{point_text}" class="line"/>
{''.join(month_labels)}
</svg>
"""
    CHART_PATH.parent.mkdir(parents=True, exist_ok=True)
    CHART_PATH.write_text(svg, encoding="utf-8")


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
        is_private = bool(repo.get("private"))
        if is_private:
            lines.append(f"| 🔒 {escape(repo['name'])} | — | 私有项目 |")
            continue
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
    git("add", "--", "README.md", "assets/contributions.svg", check=True)
    changed = git("diff", "--cached", "--quiet", "--", "README.md", "assets/contributions.svg")
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
    config = load_config()
    repositories = fetch_repositories(config)
    contributions = fetch_contributions(config["github_username"])
    write_contribution_chart(contributions)
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
