#!/usr/bin/env python3
"""
GitHub Issue Hunter
Scans top open-source data & ML repositories for targeted beginner/contributor issues.
"""

import os
import time
import argparse
import requests

REPOSITORIES = [
    "sqlfluff/sqlfluff",
    "ibis-project/ibis",
    "duckdb/duckdb",
    "dbt-labs/dbt-core",
    "pola-rs/polars",
    "pandas-dev/pandas",
    "streamlit/streamlit",
    "gradio-app/gradio",
    "great-expectations/great_expectations",
    "kedro-org/kedro",
    "astronomer/astro-sdk",
    "pallets/flask",
    "tiangolo/fastapi",
    "scikit-learn/scikit-learn",
]

DEFAULT_LABELS = ["good first issue", "help wanted"]

def get_headers(token: str | None = None) -> dict:
    headers = {"Accept": "application/vnd.github.v3+json"}
    auth_token = token or os.environ.get("GITHUB_TOKEN")
    if auth_token:
        headers["Authorization"] = f"token {auth_token}"
    else:
        print("No GITHUB_TOKEN found - unauthenticated limits apply (10 search requests/min).")
    return headers

def fetch_issues_for_query(query: str, headers: dict) -> list:
    url = "https://api.github.com/search/issues"
    params = {"q": query, "per_page": 100, "page": 1}
    issues = []

    while True:
        resp = requests.get(url, headers=headers, params=params)
        if resp.status_code == 403:
            reset_time = int(resp.headers.get("x-ratelimit-reset", time.time() + 60))
            sleep_duration = max(reset_time - int(time.time()), 1)
            print(f"Rate limit reached. Sleeping for {sleep_duration}s...")
            time.sleep(sleep_duration)
            continue
        
        if resp.status_code != 200:
            print(f"Failed query: {resp.status_code} - {resp.text[:100]}")
            break

        data = resp.json()
        items = data.get("items", [])
        issues.extend(items)
        matched_total = data.get("total_count", 0)
        print(f"    page {params['page']}: +{len(items)} issues (matched: {matched_total})")

        if len(issues) >= matched_total or len(items) == 0 or params["page"] >= 5:
            break
        params["page"] += 1
        time.sleep(1)

    return issues

def scan_repositories(target_labels: list, headers: dict) -> list:
    repos_clause = " ".join([f"repo:{repo}" for repo in REPOSITORIES])
    collected = {}

    for label in target_labels:
        print(f"\n[Scanning] Label: \"{label}\"")
        query = f'is:issue is:open label:"{label}" {repos_clause}'
        results = fetch_issues_for_query(query, headers)
        for item in results:
            collected[item["id"]] = item

    return list(collected.values())

def export_markdown(issues: list, output_file: str):
    with open(output_file, "w", encoding="utf-8") as f:
        f.write("# 🎯 Target Open Source Starter Issues\n\n")
        f.write(f"> Automatically generated on **{time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime())}**\n")
        f.write(f"> Total Available Issues Found: **{len(issues)}**\n\n")
        f.write("| Repository | Issue Title | Labels | Created |\n")
        f.write("| :--- | :--- | :--- | :--- |\n")

        for item in issues:
            repo_name = item.get("repository_url", "").split("repos/")[-1]
            title = item.get("title", "").replace("|", "\\|")
            html_url = item.get("html_url", "#")
            number = item.get("number", "")
            labels = ", ".join([f"`{lbl['name']}`" for lbl in item.get("labels", [])[:3]])
            created = item.get("created_at", "")[:10]

            f.write(f"| **[{repo_name}]({html_url})** | [{title} (#{number})]({html_url}) | {labels} | {created} |\n")

    print(f"\n Successfully wrote {len(issues)} issue(s) to {output_file}")

def main():
    parser = argparse.ArgumentParser(description="Find starter issues across open-source data libraries.")
    parser.add_argument("--output", default="target_issues.md", help="Output Markdown filepath")
    parser.add_argument("--labels", default="any", help="Comma-separated labels or 'any' for all starter labels")
    parser.add_argument("--token", default=None, help="GitHub Personal Access Token")
    args = parser.parse_args()

    if args.labels.lower() == "any":
        target_labels = DEFAULT_LABELS
    else:
        target_labels = [lbl.strip() for lbl in args.labels.split(",")]

    headers = get_headers(args.token)
    issues = scan_repositories(target_labels, headers)
    export_markdown(issues, args.output)

if __name__ == "__main__":
    main()