# Phase 0 setup: the bot Mac and the "pong" spike

This guide sets up your 2018/2019 Intel MacBook Pro as the bot's always-on relay. It then runs the spike that proves the bot can read and reply inside an iMessage **group** chat.

Confidence tags follow the plan:
- **[certain]**: verified against docs or source.
- **[likely]**: strong inference.
- **[guessing]**: unverified, so check it yourself.

---

## 0. Check the Mac first (stop if this fails)

1. Apple menu > **About This Mac**. Record the model, year and macOS version in the PR or the Phase 0 notes.
2. **You need macOS 15 Sequoia.**
   - [certain] On macOS 26 Tahoe, sending into group chats through AppleScript is broken ([BlueBubbles #777](https://github.com/BlueBubblesApp/bluebubbles-server/issues/777), [imsg #90](https://github.com/openclaw/imsg/issues/90)).
   - Already on Tahoe: stop. Group sends need the Private API or the imsg transport there, so revisit the transport decision first.
   - On Sonoma or older: upgrade to Sequoia, **not** Tahoe.
3. Stop the Mac upgrading itself:
   - Go to System Settings > General > Software Update > Automatic Updates (ⓘ).
   - Turn **off** "Install macOS updates".
   - Leave "Install Security Responses and system files" **on**.
   - Decline any "Upgrade to macOS Tahoe" prompts.
4. Look at the battery. If the trackpad clicks stiffly or the case bulges, the battery is swelling. Don't run it 24/7 in that state.

## 1. Create the bot's Apple Account (on the web, not the Mac)

1. Create a new email address just for the bot, e.g. a new Gmail `yourleague.comish@gmail.com`.
2. Go to <https://account.apple.com> and choose **Create Your Apple Account**:
   - Name: `Comish`
   - Email: the new address. This becomes the bot's iMessage address.
   - Phone: your own mobile number, for verification and two-factor. [likely] One number can be the trusted number on more than one Apple Account.
3. Verify the email and phone. Store the password in your own password manager. The bot app never sees it.

**Why a dedicated account:** if Apple ever flags it for automated sending, your personal account is untouched. You can recreate the bot account and re-add it to the groups.

## 2. Prepare the Mac

1. System Settings > Users & Groups > **Add User**:
   - Type: Standard.
   - Name: `comish`.
   - Log in as `comish` for everything below.
2. Open **Messages** and go to Settings > iMessage. Sign in with the **bot** Apple Account.
   - You don't need to sign into iCloud for the whole Mac.
   - "You can be reached at": the bot email only.
   - "Start new conversations from": the bot email.
   - Leave "Enable Messages in iCloud" off.
3. Keep it awake on power:
   - System Settings > Battery > Options: enable "Prevent automatic sleeping on power adapter when the display is off".
   - Also run in Terminal: `sudo pmset -c sleep 0 disksleep 0 displaysleep 10`
   - Keep the lid **open**. Closing it sleeps a MacBook unless an external display is attached.
4. Protect the battery. Enable Battery > "Optimized Battery Charging", or install [AlDente](https://apphousekitchen.com/) (the free tier supports charge limiting) and cap charge at about 60-80%.
5. Recover from reboots without you:
   - System Settings > Users & Groups > **Automatically log in as: comish**.
   - This requires FileVault **off**. Trade-off: the disk isn't encrypted at rest. Keep nothing on this Mac beyond the bot, and keep it at home.
   - Run `sudo systemsetup -setrestartfreeze on` to restart automatically after a system freeze.

## 3. Install BlueBubbles Server

1. Download the latest server `.dmg` from <https://github.com/BlueBubblesApp/bluebubbles-server/releases>. [certain] v1.9.9 was current as of Sep 2026. Install it.
2. Run the setup wizard:
   - Grant **Full Disk Access** and **Accessibility** when asked.
   - Set a long random **server password**. Generate one with `openssl rand -hex 24` and save it; you'll need it in step 4.
   - Skip Firebase / Google notifications. They're only for the BlueBubbles phone apps.
   - Proxy / connection method: [likely] pick the option that does **not** publish the server to the internet ("Dynamic DNS" pointing at `http://127.0.0.1:1234`). Our app talks to BlueBubbles on localhost only. Avoid ngrok or Cloudflare tunnels for now, because they expose the API with only the password protecting it.
   - **Leave the Private API off** for now.
3. In BlueBubbles Settings, enable **Start on login**.
4. You'll add the webhook in step 5, once you have a token.

## 4. Install the comish app

In Terminal, as `comish`:

```bash
xcode-select --install                               # git, if not already installed
curl -LsSf https://astral.sh/uv/install.sh | sh      # Python toolchain
git clone https://github.com/EZ8EZ/comish.git ~/comish
cd ~/comish && uv sync

# Secrets go into the macOS Keychain, never into files
uv run keyring set comish bluebubbles_password      # paste the BlueBubbles password
openssl rand -hex 24                                 # copy this output...
uv run keyring set comish webhook_token             # ...and paste it here

uv run comish bb-ping                               # expect: BlueBubbles at ...: pong
```

## 5. Create the test group and bind it

1. On your iPhone, start a new iMessage group with **2 other real people on iPhones**, so there are 3 of you. [certain] Apple only lets you add someone to a group of 3+ people where everyone is on iMessage.
2. Send one message, then open the group details and **Add Contact**: the bot's email.
3. On the Mac, run `uv run comish chats` and find the test group's `GROUP` line. Copy its GUID, e.g. `iMessage;+;chat1234...`.
4. Install the service:
   ```bash
   mkdir -p ~/comish/logs
   cp deploy/launchd/com.comish.server.plist ~/Library/LaunchAgents/
   # edit ~/Library/LaunchAgents/com.comish.server.plist:
   #   set COMISH_ALLOWED_CHAT_GUIDS to the GUID, check paths (/Users/comish/...) and uv path (`which uv`)
   launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.comish.server.plist
   curl -s http://127.0.0.1:8787/healthz              # expect {"status":"ok"}
   ```
5. In BlueBubbles, go to Settings > **API & Webhooks** > add a webhook:
   - URL: `http://127.0.0.1:8787/webhooks/bluebubbles?token=<webhook_token>`
   - Events: **New Messages**

## 6. Run the spike (exit criteria)

| Test | How | Pass |
|---|---|---|
| Basic | Anyone types `@comish ping` in the test group | `pong` appears **in the group** (not as a separate 1:1 thread) within about 10s |
| Volume | 50 pings total, spread across all 3 people (≤ 20 per person per 10 min; the rate limiter drops more) | `uv run comish spike-report` shows 50 received, 50 pongs, `missed: []`, 0 send failures, median latency < 10s |
| Negatives | Send: a message without the mention; `@comishbot hi`; a tapback on a ping; edit a ping; unsend a ping; a photo only | No replies. The report's `ignored` counts go up |
| Other chats | `@comish ping` in a 1:1 with the bot, or in another group | No reply (`unbound_chat`) |
| Reboot | Restart the Mac, touch nothing, ping again | `pong`, with no login and no manual steps |
| Soak (72h) | Ping every few hours. Tip: an iPhone Shortcuts **Time of Day** personal automation can send `@comish ping` to the group on a schedule [likely] | 0 missed across 72 hours |

**If pongs fail or land in the wrong thread:**
- Look for `send_failed` errors in `logs/events.jsonl` (e.g. AppleScript `-1728` / `-1700`).
- Record the report and that error before changing anything.

The fallback order is:
1. **BlueBubbles Private API.** This needs SIP turned off. It's a real security trade-off, so we'll decide together. Setup: <https://docs.bluebubbles.app/private-api/installation>. After that, set `COMISH_SEND_METHOD=private-api` in the plist.
2. The **imsg** transport ([openclaw/imsg](https://github.com/openclaw/imsg)). [guessing] Its bridge is untested on Intel Macs.
3. The web-page fallback channel.

When everything passes, save the `spike-report` output. That is the Phase 0 go/no-go record.
