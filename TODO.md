# Juggler TODO

Open work only; what shipped is in the git history and the release notes.
Each item: *problem → plan → cost / pushback → done when.*

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

- [ ] **Manual Test doesn't wait behind background jobs.** One scheduler thread runs every job in turn
  (`engine.py` `_run_next`), so a Test click sits on "Queued" until the running explore/reserve/sample
  batch finishes (a minute or more), then the full check (up to 60 s) and speed (up to 40 s).
  *Plan:* run dashboard tests on their own worker thread with their own xray test port range; results
  recorded under the existing lock; cap at ~10 configs per manual run.
  *Cost:* ~60 lines + tests; a second short-lived xray (~20–40 MB) only while a manual test runs.
  *Pushback:* a manual test can overlap a background batch, so more connections from one IP at once
  (see the parallelism item below).
  *Done when:* Test on an idle-looking dashboard shows Testing within ~1 s even mid-batch.
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

## P1 · Tor as an option, not only the fallback

**Problem.** Tor is used only when no config works (or a source can't be fetched otherwise), it starts
cold (minutes over Snowflake) exactly when it's needed, and there's no way to choose it.

**Plan.**
- [ ] **Route switch in the dashboard:** Auto (today) · Configs only · Tor only · Configs → Tor (Tor runs
  *through* a working config: `Socks5Proxy 127.0.0.1:20811` in torrc, so the ISP never sees Tor and no
  bridges are needed while any config works). xray already has a `tor` outbound (`xray.py` `build_main`);
  each mode is a different routing table.
- [ ] **Tor-only port** (e.g. SOCKS `127.0.0.1:20812`): always Tor, whatever the mode, for one browser
  profile; everything else stays on the fast configs. Optionally per-site: a domain list that always goes
  via Tor (same rule syntax as the whitelist).
- [ ] **Warm Tor** when any Tor mode is on: start in the background, keep its cached consensus between
  runs (reconsider `AvoidDiskWrites`), so switching is instant.
- [ ] **Bridge health:** test each bridge once, keep the ones that bootstrap, prefer WebTunnel → obfs4 →
  Snowflake → meek.

**Cost / pushback.** Tor is slow (~1–3 Mbps, high latency), YouTube plays badly, Google and others show
CAPTCHAs to Tor exits: opt-in, never the default. A warm Tor costs ~50 MB RAM and a little background
traffic. Configs → Tor still needs one working config.

**Done when.** Switching to Tor only or Configs → Tor routes traffic through Tor within seconds (no
cold bootstrap), the Tor-only port works while the rest uses configs, and Auto behaves as today.

---

## P1 · Tor fallback noise

- [ ] The log may still fill with `ERR …lyrebird…` lines. **Need a sample** of the exact lines and the
  version in use before changing anything.

---

## P1 · Monitoring

- [ ] Per-config traffic counters (bytes up/down) via the xray stats API, shown in the table.

---

## P2 · Distribution

- [ ] **Chrome Web Store listing**, so the extension installs without Developer mode.
- [ ] **Android:** public/VPS subscription first, a native app later.
- [ ] **"Start with Windows" switch** in the dashboard.
- [ ] **GitHub Actions** workflow that builds the release archives on tag push.
- [ ] **Windows Sandbox test** of the no-Python install path.
