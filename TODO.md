# Juggler TODO

Open work only; what shipped is in the git history and the release notes.
Each item: *problem → plan → cost / pushback → done when.*

---

## P0 · The Windows proxy can stay set after Juggler stops

**Problem.** After Connect, Windows points at `127.0.0.1:20809`. If Juggler stops without running its
cleanup, the setting stays, and with Juggler not running there is no internet until it's reset by hand.
Found 2026-10-21:
- **Shutdown, restart and sign-out skip the cleanup.** The console handler in `run.py` is meant to get
  `CTRL_SHUTDOWN_EVENT` / `CTRL_LOGOFF_EVENT`, but Windows doesn't send those to a console process that has
  loaded `user32.dll`/`gdi32.dll`, and Juggler loads both (via `wininet` for the proxy refresh). Confirmed
  in the running process's module list.
- **End task, a crash or a power cut** skip it too.
- Today the only repair is on the **next start** of Juggler (`Engine.start` restores it), which doesn't
  help if you don't start it again.

**Plan.** Two ways, pick one:
- [ ] **A. Self-healing proxy script (recommended).** Connect sets `AutoConfigURL =
  http://127.0.0.1:8765/proxy.pac?v=<time>` instead of a fixed `ProxyServer`. The script returns
  `PROXY 127.0.0.1:20809; DIRECT` (whitelist, `.ir` and LAN → `DIRECT`). When Juggler isn't running the
  script can't load and Windows goes direct, whatever the reason it stopped; mid-session the `; DIRECT`
  fallback covers a dead proxy. Snapshot and restore `AutoConfigURL` along with the rest.
  *Cost:* tools that read only the fixed proxy address (pip/Python `urllib`, some CLIs) stop going
  through Juggler. Browsers, Edge/Chrome, VS Code and other Electron apps follow the script.
- [ ] **B. Keep the fixed proxy and add cleanups.** While connected, register a `RunOnce` entry that
  resets the proxy at next sign-in if it still points at Juggler (covers shutdown, restart, power cut),
  plus a small watchdog outside Juggler's job object that resets it when the Juggler process disappears
  (covers End task and crashes). Every app keeps following the proxy; more moving parts.
- [ ] Either way: on start, remove a leftover `127.0.0.1:20809` proxy even when `state.json` says off
  (the state file can be stale or shared between a Windows and a WSL run).

**Done when.** Connect → shut Windows down → boot without Juggler: internet works. Connect → End task on
Juggler: browsing works again within seconds.

---

## P1 · A stable phone connection: your own Cloudflare Worker + clean IPs

**Problem.** The phone lives on the same public configs as the PC, which die within hours. Cloudflare
fronted configs also fail in waves when the Cloudflare IPs they use get throttled (2026-10-21 log:
`EOF` from `188.114.97/98.x`, `172.67.x`, `104.21.x`).

**Plan.**
- [ ] **Opt-in personal config:** the user deploys a VLESS-over-WebSocket Worker (edgetunnel / BPB-panel
  style) on a free Cloudflare account and pastes its host and UUID into Juggler once.
- [ ] **Clean-IP scan from the real connection:** test a budgeted sample of Cloudflare ranges (TLS + WS
  handshake through the Worker, latency, a short download), keep the best ~5, re-scan when they degrade.
- [ ] **Generate configs** with the clean IP as address and the Worker host as SNI/Host, and put them
  first in `/sub` (marked as yours, e.g. `🔒 Worker · 104.x · 180ms`), public configs after them.

**Cost / pushback.**
- Needs a Cloudflare account, against "no account" in the README: keep it optional with a guide.
- Free Worker plan: 100k requests/day, fine for one person, not for sharing.
- Scanning many IPs looks like scanning: small budget, spread over time, from the real connection only.

**Done when.** On a day where public configs churn, the phone stays on the Worker config without a manual
switch.

---

## P1 · Phone access

- [ ] **Stable subscription link that also works on mobile data:** a Cloudflare Tunnel (`cloudflared`) to
  your own domain, e.g. `https://sub.<yourdomain>/<secret>`; only `/sub/<secret>` exposed.
  *Pushback:* it publishes a secret URL; keep it long and rotatable.
- [ ] **Tip in the UI:** reserve the PC's IP on the router (DHCP reservation) so the URL never changes.
- [ ] **Several Jugglers on one network:** they share one public IP, so two instances double the test
  traffic and can trip ISP rate limits. Instances find each other (UDP broadcast/mDNS); one leader tests,
  followers use its pool and serve the same `/sub`.

---

## P1 · Logs and numbers that read right

From the 2026-10-21 log:
- [ ] **Fold random subdomains in the connection log.** A DNS-leak test (ipleak.net) produced hundreds of
  lines, one per random subdomain. Fold numbered/random labels under the base domain:
  `ipleak.net (×135)`.
- [ ] **Summarise background probe failures.** Every failed probe of a loaded-but-unused config prints
  `XRAY ! websocket: failed to dial …`, every ~15 s. Count them per config and print one summary, the way
  Tor noise is summarised.
- [ ] **The queue number is misleading.** Each source fetch (every 15 min) rebuilds the queue capped at
  6,000, while explore tests 48 every 90 s (~480 per fetch). So ~5,500 are never tested and "5,700 queued"
  is really "6,000 minus what was tested since the last fetch". Cap it near what can be tested before the
  next fetch, or show it as "candidates".

---

## P1 · Testing and false positives

- [ ] **Site-aware routing.** Route `youtube.com`/`googlevideo.com` via the config best at YouTube and
  `instagram.com`/`cdninstagram.com` via the best at Instagram (xray domain rules → separate balancers).
- [ ] **"Reported broken" per config:** one click demotes it hard and logs why.
- [ ] **Measure test parallelism from Iran** (now 64 handshakes at once): too many from one IP may cause
  rate limiting that makes good configs fail. Compare false negatives before and after lowering it.

---

## P1 · More sources

- [ ] **Public groups** have no `t.me/s/` web preview; reading them needs a Telegram account (Telethon).
  *Pushback:* ties scraping to a phone number, a real risk in Iran. Prefer a throwaway non-Iranian account,
  opt-in, or GitHub collectors that already scrape groups.
- [ ] **Self-maintaining channel list:** drop channels that yield nothing for 3 days; discover new ones from
  the `@handles` in config names and keep the productive ones.
- [ ] **Research pass:** more collectors, aggregators and bots with web mirrors, measured by yield from Iran.

---

## P1 · Tor fallback noise

- [ ] The log may still fill with `ERR …lyrebird…` lines. **Need a sample** of the exact lines and the
  version in use before changing anything.

---

## P1 · Monitoring

- [ ] Per-config traffic counters (bytes up/down) via the xray stats API, shown in the table.

---

## P2 · Distribution

- [ ] **New dashboard screenshot** (`docs/dashboard.png`) from a real run on Windows.
- [ ] **Chrome Web Store listing**, so the extension installs without Developer mode.
- [ ] **Android:** public/VPS subscription first, a native app later.
- [ ] **"Start with Windows" switch** in the dashboard.
- [ ] **GitHub Actions** workflow that builds the release archives on tag push.
- [ ] **Windows Sandbox test** of the no-Python install path.
