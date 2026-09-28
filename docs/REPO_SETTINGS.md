# Repository settings

These protections can't be committed as code, so apply them once in the GitHub UI. The repository is public, so all of them are free.

## 1. Protect `main` (ruleset)

Import [`.github/rulesets/protect-main.json`](../.github/rulesets/protect-main.json):

1. Go to **Settings > Rules > Rulesets > New ruleset > Import a ruleset**.
2. Choose the JSON file, review it, and click **Create**.

What it enforces on the default branch:

| Rule | Why |
|---|---|
| Changes land through a pull request | Every change is reviewable and runs CI first |
| 0 required approvals | Solo maintainer: GitHub doesn't let you approve your own PR. Raise this to 1 if a co-maintainer joins |
| All review conversations resolved | A review comment can't be merged past silently |
| Required status check `checks` (the CI job), branch up to date | Nothing merges unless lint, types, tests and the house-style check pass against the latest `main` |
| Block force pushes | History on `main` is never rewritten |
| Block deletion | `main` can't be deleted by accident |
| No bypass actors | The rules apply to everyone, including admins |

## 2. Security features

Under **Settings > Code security**, enable:

- **Secret scanning** and **Push protection**, to block commits that contain API keys or tokens. This matters because this repo is public and the bot uses a Gemini key, a Google service-account key and a BlueBubbles password. Those belong in the macOS Keychain, never in the repo.
- **Dependabot alerts** and **Dependabot security updates**. Version updates are configured in [`.github/dependabot.yml`](../.github/dependabot.yml) and run monthly.
- **Private vulnerability reporting**.

## 3. General

Under **Settings > General > Pull Requests**:

- Enable **Automatically delete head branches**.
- Allow merge commits and squash merging, and disable rebase merging. This matches the ruleset.

## 4. League data never goes in this repo

League documents, screenshots, databases and logs live on the bot Mac (`data/`, `logs/`), and both folders are gitignored. The repo holds code, fixtures made from synthetic or public data, and docs only.
