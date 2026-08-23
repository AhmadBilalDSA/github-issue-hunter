# 🔍 GitHub Issue Hunter

<p align="left">
  <a href="https://github.com/AhmadBilalDSA/github-issue-hunter/actions/workflows/update_issues.yml">
    <img src="https://github.com/AhmadBilalDSA/github-issue-hunter/actions/workflows/update_issues.yml/badge.svg" alt="Daily Scanner Status" />
  </a>
  <img src="https://img.shields.io/badge/Python-3.10%20%7C%203.11-3776AB?style=flat-square&logo=python&logoColor=white" />
  <img src="https://img.shields.io/badge/Schedule-Daily%2000%3A00%20UTC-22c55e?style=flat-square&logo=github-actions&logoColor=white" />
  <img src="https://img.shields.io/badge/License-MIT-blue?style=flat-square" />
</p>

An automated Python CLI and API scanner that surveys curated open-source data ecosystems (`polars`, `duckdb`, `scikit-learn`, `sqlfluff`, `fastapi`, and more) to discover starter issues (`good first issue`, `help wanted`, `documentation`) while respecting GitHub API rate limits.

---

## ✨ Features

- **Automated Discovery:** Queries GitHub REST API across 14+ high-impact open-source data repositories.
- **Smart Rate-Limit Handling:** Detects response status `403` / `429` and reads `x-ratelimit-reset` headers with exponential backoff.
- **Scheduled CI Integration:** A GitHub Actions cron job runs daily at `00:00 UTC` to refresh `target_issues.md` automatically.
- **Markdown Export:** Generates formatted issue summaries with direct repository links, labels, and issue numbers.

---

## 🚀 Quick Start

### 1. Installation

```bash
git clone [https://github.com/AhmadBilalDSA/github-issue-hunter.git](https://github.com/AhmadBilalDSA/github-issue-hunter.git)
cd github-issue-hunter
pip install -r requirements.txt