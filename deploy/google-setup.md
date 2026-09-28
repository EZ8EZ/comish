# Google setup: Drive access and the Gemini API key

Comish reads each league's Drive folder through a read-only **service account**, and transcribes screenshots with the **Gemini API free tier**. Both are free and need no billing account or card.

Confidence tags:
- **[certain]**: verified against Google's docs.
- **[likely]**: strong inference.

Do this on the bot Mac, signed in as the `comish` user, from the repo folder.

## 1. Create a Google Cloud project

1. Go to <https://console.cloud.google.com> and sign in with the Google account that owns the league folder.
2. Project picker > **New project**, name it `comish`, then **Create**. Don't attach a billing account.
3. **APIs & Services > Library**: search for **Google Drive API** and click **Enable**.

## 2. Create the read-only service account

1. **IAM & Admin > Service Accounts > Create service account**.
   - Name: `comish-drive-reader`.
   - Skip the optional role and access steps. It needs no project roles.
2. Open the new account, go to **Keys > Add key > Create new key > JSON**. A key file downloads.
3. Store the key in the Keychain, then delete the file:
   ```bash
   uv run python -c "import keyring, pathlib; keyring.set_password('comish', 'google_service_account_json', pathlib.Path('$HOME/Downloads/KEYFILE.json').read_text())"
   rm -P ~/Downloads/KEYFILE.json
   ```
   Replace `KEYFILE.json` with the downloaded file's name. The key never goes in the repo or any config file.
4. Copy the service account's email (`comish-drive-reader@<project>.iam.gserviceaccount.com`).

[likely] If Google blocks key creation with an organization policy, the project is under a Workspace organization. Create it under your personal Gmail account instead.

## 3. Share the league folder with it

1. In Google Drive, right-click the league folder and choose **Share**.
2. Add the service account's email as a **Viewer**. Turn off "Notify people".
3. Repeat for every league folder.

[certain] Sharing "anyone with the link" is not enough. The service account only sees files explicitly shared with it, and it sees nothing else in your Drive.

## 4. Create the Gemini API key

1. Go to <https://aistudio.google.com>, open **Get API key**, and create a key in the `comish` project.
2. Don't enable billing. The key stays on the free tier.
3. Store it:
   ```bash
   uv run keyring set comish gemini_api_key
   ```

**What the free tier means:**
- [certain] Google's pricing page says free-tier content is "used to improve our products". That includes your screenshots and, later, questions. This was accepted for v1 (docs/PLAN.md, Q1).
- Rate limits are per project and can change. If they're hit, screenshots are marked failed and retried on the next sync. Nothing is guessed.

## 5. Set the admin password

```bash
uv run keyring set comish admin_password
```

## 6. Add the league and run the first sync

```bash
uv run comish league add        # Sleeper username or league ID, Drive link, chat GUID
uv run comish sync football     # Sleeper settings plus every file in the Drive folder
uv run comish admin             # then open http://127.0.0.1:8788
```

The sync prints every file with its outcome: ingested, unchanged, changed, skipped or failed. Nothing is citable until you review it in the admin UI:

1. **Each document:** confirm the proposed date or set one, or mark it undated. Then approve it, or reject sections that are stale.
2. **Each screenshot:** compare the image against both transcriptions. Disagreements are highlighted. Correct the text, set the date, then approve.
3. **Sleeper fields:** check each value against the Sleeper app, then verify it.

Run `uv run comish review-status football` at any time to see what's citable, pending and failed.
