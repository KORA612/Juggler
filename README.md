# Juggler

**A self-hosted, free VPN for censored networks.** Juggler runs [Xray-core](https://github.com/XTLS/Xray-core) on your own machine. It keeps itself supplied with the newest public configs, tests them **from your real connection**, and routes your traffic through the ones that actually work. Tor is the fallback.

No server, no domain, no account. Clone it, double-click it, and it runs.

![Juggler dashboard](docs/dashboard.png)

## Why

Public V2Ray configs on Telegram and GitHub die within hours. Most of them don't work from your network at all. Juggler does the tedious part for you:

```
30 Telegram channels ─┐                       ┌─ stage 1: YouTube 204 through every candidate,
 (newest posts first) │                       │           in parallel; delete every failure (-1)
GitHub aggregators   ─┼─► parse ─► xray -test ┼─ verify:  ~100 KB of real youtube.com on a fresh
your own URLs        ─┘                       │           connection; drop fakes and Iranian exits
                                              └─ stage 2: speed-test the 10 best
                                                       │
           ┌───────────────────────────────────────────┘
           ▼
   live Xray balancer (top 20, health-checked, auto-failover, Tor as last resort)
   ├─ SOCKS 127.0.0.1:20808 / HTTP 127.0.0.1:20809
   ├─ Windows system proxy (one switch)
   └─ subscription URL for your phone (v2rayNG, Hiddify, …)

                               repeats every 15 minutes
```

## Features

- **Every protocol Xray can dial:** VLESS (REALITY / TLS, Vision), VMess, Trojan, Shadowsocks (AEAD and 2022), Hysteria2, WireGuard, SOCKS, HTTP. Transports: raw, ws, grpc, httpupgrade, xhttp, kcp.
- **Fresh configs first.** 30 curated Telegram channels are read three pages deep (about 60 posts each, newest first). The big GitHub dumps only fill leftover slots. Configs that failed are skipped for 6 hours, so every cycle explores new ones.
- **Hard to fool.** Liveness is a real YouTube request, not a Cloudflare ping. Cloudflare-Workers-based configs can't reach Cloudflare and would look dead. Survivors must then pull about 100 KB of youtube.com on a *separate* connection. That catches configs that pass one small request and then get killed or throttled by DPI.
- **Fast.** About 800 configs are checked in about a minute. Only the top 10 get a download test. The balancer switches to the new nodes as soon as they're verified, without waiting for the speed tests.
- **Ranked by download speed, then ping,** with flag emoji per exit country (they also render on Windows).
- **Never stuck when sources are blocked.** Each source is fetched **direct**, then through **Juggler's own proxy**, then through **Tor** (WebTunnel/obfs4 bridges you paste, plus built-in Snowflake/meek), then from the **last cached copy**.
- **Direct-route whitelist.** Iranian sites (`geosite:ir`, `.ir`, `geoip:ir`) and your LAN skip the proxy. You can add domains, IPs, CIDRs or geosite categories from the dashboard.
- **Phone support.** Your phone imports a subscription served on your Wi-Fi, with nodes renamed like `🇩🇪 DE · vless · 412ms · 3.1Mbps`.
- **Clean shutdown.** The Windows system proxy is restored on exit, on console close and after a crash. Xray and Tor never outlive the app.

## Quick start

### Windows: two clicks

1. **Get Juggler.** On GitHub press **Code → Download ZIP** and unzip it somewhere permanent, such as `Documents\Juggler`. Or, if you have git:
   ```powershell
   git clone https://github.com/KORA612/Juggler.git
   ```
2. **Double-click `Install.bat`** once. It:
   - installs Python if you don't have it (just for you, no admin needed),
   - installs the two Python packages,
   - downloads Xray, the Iran geo data and Tor (about 70 MB, only once),
   - puts a **Juggler** shortcut with the Juggler icon on your Desktop and in the Start menu.
3. **From then on, just open the Juggler shortcut.** A window with the live log opens, and so does the dashboard at **http://localhost:8765**.

If Windows asks whether to allow Juggler on networks, tick **both Private and Public**. If your phone can't connect later, the dashboard's Phone card tells you why and has an **Allow phone access** button.

### Linux

```bash
git clone https://github.com/KORA612/Juggler.git && cd Juggler
./install.sh        # .venv, packages, Xray/geo/Tor, app-menu + Desktop entry with icon
./juggler.sh        # or open "Juggler" from the app menu
```

> macOS is not wired up yet: `setup` only fetches x86_64 Windows/Linux builds.

### No GitHub access on the new machine?

GitHub downloads may be blocked. Either turn on any VPN just for the install step (the installer goes through the system proxy), or copy the `bin` folder from a machine that already has Juggler. Everything in `bin` is portable between machines with the same OS.

## Important: test from your real connection

Turn **other VPNs off** while Juggler is running. Juggler measures which configs work *from where your traffic leaves the machine*. If that is another VPN, the results describe that VPN, not your network.

## Using it

| Goal | How |
|---|---|
| Browser / any app | SOCKS5 `127.0.0.1:20808` or HTTP `127.0.0.1:20809` |
| All Windows apps | Dashboard → **System proxy** |
| Phone (v2rayNG) | ☰ → *Subscription group setting* → **+** → URL `http://<PC-LAN-IP>:8765/sub` → *Update subscription*. Scan the QR code on the dashboard or in the terminal. **Use the IP, not a hostname:** v2rayNG only accepts plain `http` for private IPs. The Phone card shows when your phone last fetched and whether Windows Firewall lets it in. |
| Phone through the PC | Set the phone's proxy to `<PC-LAN-IP>:20808` (SOCKS) or `:20809` (HTTP). |
| Pick a specific node | **Use** pins it. **Back to auto** returns to automatic lowest-ping selection. |
| Refresh now | **Refresh now** in the dashboard, or wait for the 15-minute timer. |

## Configuration

These plain-text files are created on first run. Edit them in the dashboard or with any editor.

| File | What |
|---|---|
| `sources.txt` | Telegram web previews (`https://t.me/s/<channel>`, paginated automatically) or subscription URLs (plain or base64), one per line. Ships with 30 active channels and 3 GitHub aggregators. |
| `bridges.txt` | Tor bridges from [@GetBridgesBot](https://t.me/GetBridgesBot) or [bridges.torproject.org](https://bridges.torproject.org). WebTunnel works best in Iran. |
| `whitelist.txt` | Direct-route entries: `example.com`, `full:x.com`, `keyword:bank`, `regexp:…`, `geosite:<cat>`, `geoip:<cc>`, an IP or a CIDR. Each change is validated with `xray -test` before it is applied. |

Ports, intervals and test budgets are constants at the top of `juggler/paths.py`, `juggler/engine.py` and `juggler/tester.py`.

## Security and privacy

- **Public configs are strangers' servers.** The operator can see your IP and which sites you visit; HTTPS content stays private. Avoid banking and other sensitive logins through them, and use Tor Browser for anything that must stay anonymous.
- **Safe mode** (on by default) drops configs that send traffic over the wire unencrypted: VLESS, Trojan, SOCKS or HTTP without TLS or REALITY, and Shadowsocks `none`. Configs with `allowInsecure` are always dropped, because Xray refuses them.
- **The dashboard API is localhost-only.** Other LAN devices can reach `/sub` and the proxy ports, nothing else.
- **No telemetry.** Juggler only talks to your sources, the test targets (`youtube.com`, `cloudflare.com`, `cachefly.net`, plus `api.country.is` / `ipinfo.io` for exit countries) and GitHub / torproject.org (first-run downloads). Test traffic goes *through* the configs being tested.

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| Very few configs alive | Normal on heavily filtered networks; survivors are kept across cycles. Add more sources. |
| Sources show `CACHE` | Every fetch path failed. Add working WebTunnel bridges so the Tor path can work. |
| Tor stuck below 100% | The built-in bridges are blocked. Paste fresh WebTunnel bridges. Bridges whose domain resolves to `10.10.34.x` are DNS-poisoned and won't work. |
| v2rayNG says "Failure" | Check the dashboard's Phone card. **"Firewall blocks phones on this Private network"** means Windows was only allowed on Public networks, so press **Allow phone access**. If it says the phone reached Juggler but there were no working configs, wait for the first test cycle. Also open the URL in the phone's browser: you should see a long block of text. Some office Wi-Fi networks block devices from reaching each other. |
| `port 8765 busy` | Juggler is already running in another window. |

## Development

```bash
python -m venv .venv && .venv/bin/pip install -r requirements.txt pytest
.venv/bin/python -m juggler.setup     # download xray / geo / tor into bin/
.venv/bin/python -m pytest -q         # the xray-backed tests run once bin/xray exists
```

```
Install.bat         Windows one-time installer (runs scripts/install.ps1)
Juggler.bat         Windows launcher, the target of the shortcuts
install.sh / juggler.sh   the same pair for Linux
run.py              entry point: setup → engine → Flask
juggler/links.py    share link ⇄ Xray outbound (pure, heavily tested)
juggler/sources.py  fetch cascade: direct → own proxy → Tor → disk cache
juggler/tester.py   YouTube liveness → real-use verify → speed, via one temporary xray (an HTTP inbound per node)
juggler/xray.py     config builders, validation (bisection), main instance + API
juggler/engine.py   refresh loop, state, dashboard actions
juggler/tor.py      Tor + lyrebird (WebTunnel / obfs4 / Snowflake / meek)
juggler/sysproxy.py Windows system proxy with snapshot and restore
juggler/firewall.py Windows Firewall diagnosis + one-click (UAC) allow rule for phones
juggler/procs.py    child processes die with the app (Windows Job Object)
juggler/app.py      Flask routes, SSE log stream, QR codes, /sub
```

## Credits

- [Xray-core](https://github.com/XTLS/Xray-core) (MPL-2.0)
- [Tor](https://www.torproject.org/) and lyrebird
- [Iran-v2ray-rules](https://github.com/Chocolate4U/Iran-v2ray-rules) for the geo data
- Flag emoji on Windows: [Twemoji Country Flags](https://github.com/talkjs/country-flag-emoji-polyfill) (graphics CC-BY 4.0 © Twitter/X; packaging MIT)

Juggler is a tool for getting around censorship. Respect the laws and risks where you live.
