# Juggler TODO

Feedback after two weeks of daily use (2026-10-19). **P0** = fixes the "it keeps dropping me" experience.
Each item: *problem → plan → cost / pushback → done when.*

> **v1.5.0 (2026-10-19):** perpetual scored engine, per-site matrix, sticky in-use config with live xray
> updates, outage guard, false-negative sampling, paste / Test / Test all, `/ping`, Tor noise summary,
> Chrome/Edge extension. Items below marked [x] shipped in it.

---

## P0 · Ship what's already fixed

- [x] **Ship the memory fix** (in v1.5.0). Every HTTPS test request built a fresh TLS context (CA store loaded into OpenSSL each time). That held ~1 GB RAM forever. With one shared context it's ~340 MB peak, ~210 MB idle. The fix is in `tester.py`, `sources.py` and `engine.py`, uncommitted, and is **not** in the v1.4.0 release.

---

## P0 · Perpetual scoring engine (replaces the 15-minute reset)

**Problem.** Every 15 minutes the whole pool is re-tested once, and anything that fails that single test is dropped. One flaky handshake, a DPI hiccup or a 5-second stall throws away a config that worked all morning. The balancer is rebuilt and the active connection is cut, even when the old config was fine.

**Plan.**
- [x] **A persistent pool with a score per config**, stored in `state.json` and surviving restarts:
  - `success_ewma`: rolling success rate, recent checks weigh more.
  - `latency_ms_ewma`, `throughput_ewma`.
  - `sites{youtube, google, instagram, telegram}` (see Testing).
  - `last_ok`, `consecutive_fails`, `first_seen`, `source`, `manual` flag.
  - **The score combines reliability first, site coverage second, latency third and speed last.** Speed must never outrank "it actually loads".
- [x] **Continuous, budgeted testing instead of cycles:**
  - **Active set (~20):** cheap liveness probe each, every ~60 s, staggered so it's never a burst.
  - **Warm reserve (~50–100):** full check every ~10 min.
  - **Explore:** a trickle of new candidates, e.g. 50 every 2 min, instead of 800 at once.
  - **Fetching sources** stays on its own timer (15–30 min) and only *adds* candidates; it never resets the pool.
- [x] **Hysteresis, no single-failure kills:**
  - A config is demoted gradually and removed only after K consecutive failures spread over time (e.g. 3 failures across 10+ min), or when its score drops below a floor.
  - A failed check is retried once at once with a longer timeout before it counts. Iranian links have high latency spikes.
- [x] **A sticky active config.**
  - Never switch away from a config that is still passing just because a "faster" one appeared.
  - Switch only when the current one degrades, or a candidate beats it by a clear margin (e.g. +25 % score) *and* has been stable for N checks.
  - Use `xray api bo` to pin the chosen config, and keep the balancer as instant failover underneath.
- [x] **Changes to the main Xray become additive.**
  - Add or remove outbounds with `xray api ado/rmo` instead of restarting the process, so open connections (YouTube video, calls) survive.
  - Fall back to a restart only when the whitelist changes.

**Cost.** CPU and RAM are about the same as today. Bandwidth **goes down**: today's 800-config burst is ~100–230 MB per cycle, mostly the 100 KB verify on every survivor. Budget target: **< 150 MB/hour** in normal mode, plus a "data saver" mode (< 30 MB/hour) for metered connections.

**Done when.** A single failed check never removes a config. The active config changes only when it's actually degrading. The log shows *why* every switch happened.

---

## P0 · Testing that matches real use

**Problem.** Configs that ping fine or show 12 Mbps don't load YouTube. Some load YouTube but not Instagram, Google Search or Telegram. Ranking by speed promoted exactly those configs and replaced a slower one that worked.

**Plan.**
- [x] **A per-site connectivity matrix.** For each candidate that passes liveness, run small *real* requests, reading at most ~32 KB each:
  - YouTube: `www.youtube.com/generate_204`, plus the first bytes of the real page (already done).
  - Google: `www.google.com/search?q=test` (often blocked differently from YouTube).
  - Instagram: `www.instagram.com/` (header + first chunk).
  - Telegram: `web.telegram.org/` and a raw TCP connect to a Telegram data centre (149.154.167.51:443) through the proxy, since the Telegram app doesn't speak HTTPS to the web.
  - Store the result as site chips per config (`YT ✓ G ✓ IG ✗ TG ✓`) and weight it into the score.
  - **Configurable "must work" sites:** e.g. a config must pass YouTube + Google to enter the active set.
- [x] **A sustained test, not a burst.** Stream for ~3–5 s and record time-to-first-byte, stalls and throughput variance. A config that starts at 12 Mbps and stalls after 300 KB scores low.
- [x] **Repeat, don't trust a single run.** All metrics become EWMAs over many checks (see the scoring engine).
- [ ] **(later) Site-aware routing.** Route `youtube.com`/`googlevideo.com` via the config best at YouTube and `instagram.com`/`cdninstagram.com` via the best at Instagram. Xray supports this with domain rules → separate balancers. This is how "works for YT but not IG" turns from a bug into a feature.

**Pushback / cost.**
- **The matrix runs only on configs that are already alive:** about 4 requests × ~30 KB × ~100 configs ≈ 12 MB per pass, every 10–15 min ≈ 50 MB/hour. It does **not** run on all 800 candidates; that would be ~100 MB per pass and would look like a scanner to the ISP.
- **Telegram via raw TCP is a weaker check** than a real MTProto handshake, but good enough to tell "DC reachable" from "blocked".

---

## P0 · False positives / false negatives

**False positives** (shows as working, doesn't work). Likely causes:
1. **Time:** it worked at test time and was blocked minutes later.
2. **Per-site blocking** (see the matrix).
3. **Throttling** after the first few hundred KB.
4. **DPI that resets long-lived connections only.**

The engine and the matrix above address all four:
- [ ] Add a **"reported broken"** button per config: one click demotes it hard, and the reason is logged.

**False negatives** (working configs we throw away). These are probably large today:
- [x] **One shot, 6 s timeout:** Iranian RTT spikes beyond that are common. → retry failures once with a 12 s timeout.
- [ ] **192 parallel handshakes from one IP** *(now 64; still to be measured from Iran)* may trigger ISP rate-limiting or packet loss, which makes *good* configs fail. → lower parallelism (64) with the trickle-explore design. **Measure** before and after.
- [x] **6 h dead TTL is a cliff.** → exponential backoff instead: retry after 30 min, 2 h, 6 h, 24 h.
- [x] **800-candidate cap:** most of the ~6,000 unique configs are never tested at all. → trickle exploration eventually covers everything.
- [x] **Measure it:** each hour, re-test a random sample of 30 "dead" configs with relaxed settings. The revival rate is our false-negative estimate; show it in the dashboard.

---

## P1 · Panel features

- [x] **Paste configs by hand.** A text area in the dashboard accepts one or many share links (or a base64 subscription). They're parsed, validated with `xray -test`, tested at once, and stored as `manual`. Manual configs are never auto-deleted, only marked failing.
- [x] **Per-config Test button.** Runs the full matrix on that config now, and shows the site chips plus latency and throughput live in the row.
- [x] **"Test all" button.** Runs the matrix over the whole pool with the budget limits above, and shows a progress bar.
- [ ] **Site chips column** in the configs table *(done; sortable)*, and a filter like "only configs that load Instagram".

---

## P1 · Phone access is flaky

**Symptoms.** Typing `192.168…`/`172…` into v2rayNG works sometimes. Some laptops never work, even with the firewall allowed.

Likely causes, in order:
1. **The PC's LAN IP changes** (DHCP lease, other Wi-Fi, VPN adapter up or down), so the saved subscription URL points at nothing.
2. **The wrong network adapter is shown.** `lan_ip()` picks the default-route interface, which can be a VPN TUN or a Hyper-V/WSL vEthernet adapter. Laptops with VPN clients do this.
3. **Client isolation** on the router or guest/office Wi-Fi: phone and PC can't see each other at all.
4. **Third-party firewall or antivirus** (ESET, Kaspersky, Norton…) blocking it even when Windows Firewall allows it.

**Plan.**
- [x] **Show every LAN address** in the Phone card, each with its own QR code, and label the adapter (Wi-Fi / Ethernet / virtual).
- [x] **Self-test page:** opening `http://<ip>:8765/ping` on the phone shows "✓ this phone can reach Juggler". It separates "can't reach" from "reached, but no configs".
- [ ] **Stable link that also works on mobile data:** a **Cloudflare Tunnel** (`cloudflared`) to your own domain, e.g. `https://sub.<yourdomain>/<secret>`.
  - Free, HTTPS, the same URL whatever the PC's IP, and it works away from home.
  - Only `/sub/<secret>` is exposed; nothing else.
  - **Pushback:** this publishes a (secret) URL on the internet. Keep the secret long and rotatable.
- [ ] **Tip in the UI:** reserve the PC's IP on the router (DHCP reservation) so the URL never changes.

**Several Jugglers on one network?** They don't conflict directly: ports are per machine, and each tests independently. But:
- **They share one public IP,** so two instances double the test traffic (800+ handshakes per cycle each). That can trigger ISP rate-limiting or per-IP connection limits on free servers, which causes *more* false negatives for both.
- **A phone can easily be subscribed to the "other" Juggler's IP.**
- [ ] **LAN coordination:** instances find each other (UDP broadcast/mDNS). One leader tests; followers consume its scored pool and serve the same `/sub`. Each PC still routes its own traffic locally.

---

## P1 · More sources

- [ ] **Public groups.** Confirmed 2026-10-19: groups have **no** `t.me/s/` web preview (it redirects); only channels do. Reading groups needs a real Telegram account through the API (Telethon + api_id/hash + phone login).
  - **Pushback:** that ties scraping to your phone number and account, which in Iran is a real privacy and safety risk.
  - Prefer: a dedicated throwaway account with a non-Iranian number, opt-in only, with a big warning. Or find GitHub collectors that already scrape groups and use their output.
- [ ] **Self-maintaining channel list:**
  - Track working-configs-per-channel over time and automatically drop channels that yield nothing for 3 days.
  - Discover new channels from the `@handles` inside config names, probe them, and keep the productive ones. This is how the current 30 were found, manually, on 2026-10-03.
- [ ] **Research pass for more sources:** more GitHub collectors (ones that include groups), plus subscription aggregators and bots with web mirrors. Measure each source by its *yield from Iran*, not its link count.

---

## P1 · Tor fallback noise

- [ ] **User report:** the log still fills with `ERR …lyrebird…` lines. **Need a sample** of the exact lines and the version in use (v1.4.0 summarises `[warn]` lines, but still prints every `[err]` line).
- [x] **Likely fix:**
  - Treat lyrebird/PT `[err]` lines like warnings: count them, one summary line per Tor session.
  - Validate `bridges.txt` on save, flag bad lines (e.g. an empty `url=`), and skip them.
  - Optionally drop bridges whose domain resolves to `10.10.34.x` (Iranian DNS poisoning).

---

## P2 · Distribution

- [x] **Chrome / Edge extension (companion)**, see `extension/`. Done 2026-10-19:
  - Routes only the browser through Juggler (no system proxy), with the whitelist as a bypass list.
  - Shows status, lets you switch configs, and refreshes.
  - **Limit:** an extension can't run Xray itself (browsers only speak HTTP/HTTPS/SOCKS proxies), so Juggler must be running on the PC.
- [ ] **Chrome Web Store listing.** Outside the store, Chrome only installs extensions through Developer mode → *Load unpacked*.
- [ ] **Android:** public/VPS subscription first, then the native app later (see the discussion of 2026-10-06).
- [ ] **"Start with Windows" switch** in the dashboard.
- [ ] **GitHub Actions** workflow that builds the release archives on tag push.
- [ ] **Windows Sandbox test** of the no-Python install path.
