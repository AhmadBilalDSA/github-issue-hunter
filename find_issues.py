#!/usr/bin/env python3
"""Find open "good first issue" / "help wanted" issues in high-impact repos.

Queries the GitHub Search API (https://api.github.com/search/issues) for open
issues carrying the ``good first issue`` and ``help wanted`` labels across a
curated list of high-impact repositories, then writes the results to
``target_issues.md`` as a clean Markdown table.

Features
--------
* One consolidated search query per label set (few API round-trips).
* Optional authentication via the ``GITHUB_TOKEN`` environment variable, which
  lifts the search rate limit from 10 to 30 requests/minute.
* Primary rate-limit handling: sleeps until ``X-RateLimit-Reset`` when the
  quota is exhausted; honors ``Retry-After`` for secondary/abuse limits.
* Exponential backoff with jitter for transient network and 5xx errors.
* Pipe-safe Markdown escaping so odd titles cannot break the table.

Usage
-----
    python find_issues.py                      # writes ./target_issues.md
    python find_issues.py --output report.md   # custom destination
    python find_issues.py --labels any         # issues with EITHER label
"""

from __future__ import annotations

import argparse
import os
import random
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from typing import Any

import requests

# --------------------------------------------------------------------------- #
# Configuration
# --------------------------------------------------------------------------- #

SEARCH_URL = "https://api.github.com/search/issues"

#: Repositories to scan, in display order.
REPOS: list[str] = [
    # Data engineering / SQL / transformation
    "sqlfluff/sqlfluff",
    "ibis-project/ibis",
    "duckdb/duckdb",
    "dbt-labs/dbt-core",
    "pola-rs/polars",          # canonical Polars repo ("polars-dev/polars" is a 404)
    "pandas-dev/pandas",
    # Apps / dashboards
    "streamlit/streamlit",
    "gradio-app/gradio",
    # Data quality / pipelines
    "great-expectations/great_expectations",
    "kedro-org/kedro",
    "astronomer/astro-sdk",    # NOTE: archived upstream; kept per spec
    # Web frameworks
    "pallets/flask",
    "tiangolo/fastapi",
    # Machine learning
    "scikit-learn/scikit-learn",
]

#: Labels applied to every search (as GitHub label qualifiers).
LABELS: list[str] = ["good first issue", "help wanted"]

PER_PAGE = 100          # Search API maximum per page.
MAX_PAGES = 10          # Hard ceiling (the API never serves more than 1000 hits).
MAX_ATTEMPTS = 4        # Attempts per page before giving up.
BACKOFF_BASE = 2.0      # Base delay for exponential backoff (seconds).
BACKOFF_CAP = 60.0      # Longest single backoff sleep (seconds).
RESET_SLEEP_CAP = 900   # Never sleep more than 15 min awaiting a quota reset.
HTTP_TIMEOUT = 30       # Per-request timeout (seconds).
MAX_TITLE_LEN = 110     # Titles are truncated to keep the table readable.

_session = requests.Session()


class GitHubSearchError(RuntimeError):
    """Raised when the issue search cannot be completed."""


# --------------------------------------------------------------------------- #
# Query construction
# --------------------------------------------------------------------------- #


def build_queries(label_mode: str) -> list[str]:
    """Return the search query strings to run.

    ``all`` requires every label (GitHub's comma-separated
    ``label:"a",label:"b"`` syntax behaves the same way). ``any`` matches
    issues carrying at least one label; GitHub has no OR operator, so this
    runs one query per label and merges the results.
    """
    if label_mode == "all":
        label_groups = [" ".join(f'label:"{label}"' for label in LABELS)]
    else:  # "any"
        label_groups = [f'label:"{label}"' for label in LABELS]

    queries = []
    for labels in label_groups:
        parts = ["is:issue", "is:open", labels]
        parts += [f"repo:{repo}" for repo in REPOS]
        queries.append(" ".join(parts))
    return queries


def build_headers(token: str | None) -> dict[str, str]:
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "find-issues-script/1.0",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


# --------------------------------------------------------------------------- #
# HTTP layer with rate-limit handling and retries
# --------------------------------------------------------------------------- #


def _header_float(response: requests.Response, name: str, default: float) -> float:
    """Read a numeric header, falling back to ``default`` if missing/invalid."""
    try:
        return float(response.headers.get(name))
    except (TypeError, ValueError):
        return default


def _sleep_until_reset(response: requests.Response) -> None:
    """Sleep until the primary rate-limit window resets (plus a small buffer)."""
    reset_at = _header_float(response, "X-RateLimit-Reset", time.time() + 60)
    delay = min(max(reset_at - time.time(), 0) + 2, RESET_SLEEP_CAP)
    print(f"    ! rate limit exhausted; sleeping {delay:.0f}s until reset", flush=True)
    time.sleep(delay)


def fetch_search_page(
    query: str, page: int, headers: dict[str, str]
) -> dict[str, Any]:
    """Fetch one page of search results, retrying transient failures.

    Raises GitHubSearchError for non-retryable client errors or when all
    attempts for this page are exhausted.
    """
    params = {
        "q": query,
        "per_page": PER_PAGE,
        "page": page,
        "sort": "created",
        "order": "desc",
    }
    last_error: Exception | None = None

    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            response = _session.get(
                SEARCH_URL, headers=headers, params=params, timeout=HTTP_TIMEOUT
            )

            # Primary rate limit exhausted -> wait for the window to reset.
            if (
                response.status_code in (403, 429)
                and response.headers.get("X-RateLimit-Remaining") == "0"
            ):
                _sleep_until_reset(response)
                continue

            # Secondary limit / abuse detection -> honor Retry-After.
            if response.status_code in (403, 429) and response.headers.get("Retry-After"):
                delay = min(_header_float(response, "Retry-After", 30.0), BACKOFF_CAP)
                print(f"    ! secondary limit; backing off {delay:.0f}s", flush=True)
                time.sleep(delay)
                continue

            if 500 <= response.status_code < 600:
                # Transient server error: fall through to the retry path.
                response.raise_for_status()

            # Remaining 4xx responses (bad query, bad token, ...) are fatal.
            response.raise_for_status()
            return response.json()

        except requests.HTTPError as exc:
            resp = exc.response
            status = resp.status_code if resp is not None else "??"
            if resp is not None and status < 500:
                snippet = (resp.text or "").strip()[:300]
                raise GitHubSearchError(
                    f"GitHub rejected the request (HTTP {status}): {snippet}"
                ) from exc
            last_error = exc
        except (requests.ConnectionError, requests.Timeout) as exc:
            last_error = exc
        except ValueError as exc:  # malformed JSON body
            last_error = GitHubSearchError(f"malformed JSON in response: {exc}")

        if attempt < MAX_ATTEMPTS:
            delay = min(
                BACKOFF_BASE * 2 ** (attempt - 1) + random.uniform(0, 1), BACKOFF_CAP
            )
            print(
                f"    ! attempt {attempt}/{MAX_ATTEMPTS} failed ({last_error}); "
                f"retrying in {delay:.1f}s",
                flush=True,
            )
            time.sleep(delay)

    raise GitHubSearchError(
        f"giving up on page {page} after {MAX_ATTEMPTS} attempts: {last_error}"
    )


def collect_issues(queries: list[str], headers: dict[str, str]) -> list[dict[str, Any]]:
    """Run every query, following pagination; de-duplicate by issue id."""
    seen_ids: set[int] = set()
    items: list[dict[str, Any]] = []

    for q_index, query in enumerate(queries, start=1):
        print(f"[query {q_index}/{len(queries)}] {query}", flush=True)
        for page in range(1, MAX_PAGES + 1):
            payload = fetch_search_page(query, page, headers)
            batch = payload.get("items", []) or []
            fresh = [item for item in batch if item.get("id") not in seen_ids]
            seen_ids.update(item.get("id") for item in fresh)
            items.extend(fresh)
            total = payload.get("total_count", "?")
            print(f"    page {page}: +{len(fresh)} issues (matched: {total})", flush=True)
            if len(batch) < PER_PAGE:
                break
    return items


# --------------------------------------------------------------------------- #
# Markdown rendering
# --------------------------------------------------------------------------- #


def repo_full_name(item: dict[str, Any]) -> str:
    url = item.get("repository_url", "") or ""
    parts = url.rstrip("/").split("/")
    return "/".join(parts[-2:]) if len(parts) >= 2 else "unknown/unknown"


def md_cell(text: str) -> str:
    """Make arbitrary text safe inside a Markdown table cell."""
    cleaned = str(text).replace("\r", " ").replace("\n", " ")
    return cleaned.replace("|", "\\|").strip()


def fmt_title(raw: str) -> str:
    title = md_cell(raw)
    if len(title) > MAX_TITLE_LEN:
        title = title[: MAX_TITLE_LEN - 3].rstrip() + "..."
    return title or "(untitled)"


def render_markdown(
    items: list[dict[str, Any]], queries: list[str], label_mode: str
) -> str:
    """Render the collected issues as a clean Markdown report."""
    generated = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    mode_text = "all labels required" if label_mode == "all" else "any label"
    repo_rank = {name: idx for idx, name in enumerate(REPOS)}

    # Newest first within each repo (stable two-pass sort).
    items = sorted(items, key=lambda it: str(it.get("created_at", "")), reverse=True)
    items = sorted(items, key=lambda it: repo_rank.get(repo_full_name(it), len(REPOS)))

    counts = Counter(repo_full_name(it) for it in items)
    summary = ", ".join(
        f"{repo} ({counts[repo]})" for repo in REPOS if counts.get(repo)
    )

    lines = [
        "# Target Issues: Good First Issue / Help Wanted",
        "",
        f"> Generated **{generated}** - filter: `{mode_text}` - "
        f"scanned {len(REPOS)} high-impact repos.",
        "",
        f"**{len(items)} open issue(s)** found.",
    ]
    if summary:
        lines += ["", f"Per-repo counts: {summary}"]

    if not items:
        lines += [
            "",
            "No open issues matched the current filters. Try:",
            "",
            "- `python find_issues.py --labels any` (match either label instead of both),",
            "- setting `GITHUB_TOKEN` to rule out rate-limit noise,",
            "- widening the `REPOS` list.",
        ]
    else:
        lines += [
            "",
            "| Repo | Title | Link | Comments | Created Date |",
            "|------|-------|------|----------|--------------|",
        ]
        for item in items:
            repo = md_cell(repo_full_name(item))
            title = fmt_title(item.get("title", ""))
            link = item.get("html_url", "")
            comments = item.get("comments", 0)
            created = str(item.get("created_at", ""))[:10]
            link_cell = f"[open]({link})" if link else "-"
            lines.append(
                f"| {repo} | {title} | {link_cell} | {comments} | {created} |"
            )

    lines += ["", "---", "", "### Search queries used", ""]
    lines += [f"- `{query}`" for query in queries]
    lines.append("")
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# Entry point
# --------------------------------------------------------------------------- #


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Collect open starter issues from high-impact GitHub repos."
    )
    parser.add_argument(
        "--output",
        default="target_issues.md",
        help="destination Markdown file (default: %(default)s)",
    )
    parser.add_argument(
        "--labels",
        choices=("all", "any"),
        default="all",
        help="require every label ('all') or at least one ('any'); default: %(default)s",
    )
    parser.add_argument(
        "--token",
        default=None,
        help="GitHub token (defaults to the GITHUB_TOKEN / GH_TOKEN env vars)",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    token = args.token or os.getenv("GITHUB_TOKEN") or os.getenv("GH_TOKEN")
    if token:
        print("Using authenticated requests (higher search rate limit).", flush=True)
    else:
        print(
            "No GITHUB_TOKEN found - unauthenticated limits apply "
            "(10 search requests/min).",
            flush=True,
        )

    headers = build_headers(token)
    queries = build_queries(args.labels)

    try:
        items = collect_issues(queries, headers)
    except GitHubSearchError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\ninterrupted", file=sys.stderr)
        return 130

    markdown = render_markdown(items, queries, args.labels)
    with open(args.output, "w", encoding="utf-8") as fh:
        fh.write(markdown)

    print(f"Wrote {len(items)} issue(s) to {args.output}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
