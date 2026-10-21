"use strict";
const $ = (s) => document.querySelector(s);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const regionName = (() => {
  try { const d = new Intl.DisplayNames(["en"], { type: "region" }); return (cc) => { try { return d.of(cc); } catch { return cc; } }; }
  catch { return (cc) => cc; }
})();

let S = null;               // last /api/state
let sort = { key: "rank", dir: 1 };
let logLevel = "all";

// ---------- theme ----------
const savedTheme = (() => { try { return localStorage.getItem("theme"); } catch { return null; } })();
if (savedTheme) document.documentElement.dataset.theme = savedTheme;
else if (matchMedia("(prefers-color-scheme: light)").matches) document.documentElement.dataset.theme = "light";
$("#theme").onclick = () => {
  const next = document.documentElement.dataset.theme === "light" ? "dark" : "light";
  document.documentElement.dataset.theme = next;
  try { localStorage.setItem("theme", next); } catch {}
};

// ---------- helpers ----------
async function api(path, method = "GET", body) {
  const r = await fetch(path, { method, headers: body ? { "Content-Type": "application/json" } : {}, body: body ? JSON.stringify(body) : undefined });
  let data = {};
  try { data = await r.json(); } catch {}
  if (!r.ok) throw new Error(data.msg || `HTTP ${r.status}`);
  return data;
}
function toast(msg, err = false) {
  const t = document.createElement("div");
  t.className = "toast" + (err ? " err" : "");
  t.textContent = msg;
  $("#toasts").appendChild(t);
  setTimeout(() => t.remove(), 3200);
}
async function copy(text, what = "Copied") {
  try { await navigator.clipboard.writeText(text); }
  catch {
    const ta = Object.assign(document.createElement("textarea"), { value: text });
    document.body.appendChild(ta); ta.select(); document.execCommand("copy"); ta.remove();
  }
  toast(`✓ ${what}`);
}
const qrSrc = (d) => "/api/qr?d=" + encodeURIComponent(d);
const fmtSpeed = (k) => (k > 0 ? (k >= 1000 ? (k / 1000).toFixed(1) + " Mbps" : k + " kbps") : null);
const pingColor = (ms) => (ms < 400 ? "var(--accent)" : ms < 900 ? "var(--amber)" : "var(--red)");
function ago(ts) {
  if (!ts) return "never";
  const s = Math.max(0, Math.round(Date.now() / 1000 - ts));
  if (s < 60) return `${s}s ago`;
  if (s < 3600) return `${Math.round(s / 60)} min ago`;
  return `${Math.round(s / 3600)} h ago`;
}

// ---------- render ----------
function renderHero() {
  $(".hero").classList.toggle("on", !!S.sysproxy.on);
  const n = S.nodes.find((x) => x.id === S.active) || (S.mode === "nodes" ? S.nodes.find((x) => x.in_balancer) : null);
  const orb = $("#orb");
  orb.className = "orb " + (S.mode === "nodes" ? "on" : S.mode === "tor" ? "tor" : "off");
  const eb = $("#hero-eyebrow");
  const chips = [];
  if (n) {
    $("#hero-flag").textContent = n.flag;
    // "Connected" belongs to the power button; without the system proxy the route is only ready
    eb.className = "eyebrow"; eb.textContent = (S.sysproxy.on ? "Connected" : "Ready") + (S.pinned ? " · pinned" : "");
    $("#hero-title").textContent = `${regionName(n.country)} · ${n.proto.toUpperCase()}`;
    chips.push(`<span class="chip good">● <b>${n.ping_ms} ms</b></span>`);
    const sp = fmtSpeed(n.speed_kbps);
    if (sp) chips.push(`<span class="chip"><b>${sp}</b></span>`);
    chips.push(`<span class="chip">${esc(n.net)} · ${esc(n.sec)}</span>`);
    if (n.exit_ip) chips.push(`<span class="chip">exit <b>${esc(n.exit_ip)}</b></span>`);
  } else if (S.mode === "tor") {
    $("#hero-flag").textContent = "🧅";
    eb.className = "eyebrow tor"; eb.textContent = S.tor.ready ? "Fallback · Tor" : "Waiting";
    $("#hero-title").textContent = S.tor.ready ? "No configs yet. Traffic goes through Tor" : `Tor is bootstrapping… ${S.tor.progress}%`;
  } else {
    $("#hero-flag").textContent = "⏳";
    eb.className = "eyebrow warn"; eb.textContent = "No route";
    $("#hero-title").textContent = "Searching for working configs…";
  }
  chips.push(`<span class="chip indigo">loaded <b>${S.nodes.filter((x) => x.in_balancer).length}</b></span>`);
  const sw = S.last_switch;
  if (sw && sw.reason && Date.now() / 1000 - sw.at < 3600)
    chips.push(`<span class="chip warn" title="${esc(sw.reason)}">switched ${ago(sw.at)}: ${esc(sw.reason.slice(0, 48))}</span>`);
  chips.push(`<span class="chip">sources <b>${ago(S.last_refresh)}</b></span>`);
  $("#hero-chips").innerHTML = chips.join("");
  $("#subtitle").textContent = S.xray ? `socks :20808 · http :20809 · ${S.mode === "nodes" ? "online" : S.mode}` : "xray is not running";

  const phases = { fetching: "Fetching sources", exploring: "Exploring new configs", checking: "Checking configs",
                   testing: "Testing", sitecheck: "Checking a site", speed: "Measuring speed", sampling: "Re-checking retired configs", starting: "Starting" };
  const ph = $("#phase");
  ph.textContent = phases[S.phase] || "";
  ph.classList.toggle("show", !!phases[S.phase]);
}

function renderConnlog() {
  const c = S.connlog || {};
  const b = $("#connlog");
  b.classList.toggle("recording", !!c.on);
  b.textContent = c.on ? `● Recording · ${c.count.toLocaleString()} conn.` : "● Record connections";
}

function renderControls() {
  renderConnlog();
  const sp = $("#sysproxy"), on = S.sysproxy.on, ok = S.sysproxy.supported;
  sp.setAttribute("aria-checked", on);
  sp.disabled = !ok;
  sp.title = !ok ? "" : on ? "Disconnect: restore the Windows proxy settings" : "Connect: route Windows apps through Juggler";
  $("#ctl-sysproxy").classList.toggle("disabled", !ok);
  const label = $("#sysproxy-label");
  label.textContent = !ok ? "System proxy" : on ? "Connected" : "Connect";
  label.classList.toggle("on", ok && on);
  $("#sysproxy-desc").textContent = ok ? ""
    : S.sysproxy.wsl ? "Juggler is running in WSL. Start it on Windows (Juggler.bat) to use Connect"
    : "Windows only. Point apps at 127.0.0.1:20808";
  const sm = $("#safemode");
  sm.setAttribute("aria-checked", S.safe_mode);
  sm.title = S.safe_mode ? "Safe mode on: configs that send traffic unencrypted are dropped. Click to turn off"
    : "Safe mode off: unencrypted configs are allowed. Click to turn on";
  $("#safemode-state").textContent = S.safe_mode ? "On" : "Off";
  const st = S.stats || {};
  const steps = [["links", "links found"], ["queued", "queued"], ["pool", "in pool"], ["eligible", "working"], ["active", "loaded"]];
  $("#funnel").innerHTML = steps.map(([k, label], i) => {
    const v = st[k] ?? "–";
    return `<div class="step${i >= 3 ? " final" : ""}"><b>${typeof v === "number" ? v.toLocaleString() : v}</b><span>${label}</span></div>`;
  }).join("");
}

const speedColor = (k) => (k >= 3000 ? "var(--accent)" : k >= 1000 ? "var(--amber)" : "var(--red)");

function sortedNodes() {
  const site = selectedSite();
  const arr = S.nodes.map((n, i) => ({ ...n, rank: i + 1, _health: -n.health,
    _site: site && typeof site.results[n.id] === "number" ? site.results[n.id] : Infinity }));
  const k = { health: "_health", site: "_site" }[sort.key] || sort.key;
  arr.sort((a, b) => {
    let x = a[k], y = b[k];
    if (k === "speed_kbps") { x = x > 0 ? -x : 0; y = y > 0 ? -y : 0; }   // fastest first
    if (k === "country") { x = regionName(x); y = regionName(y); }
    return (x > y ? 1 : x < y ? -1 : 0) * sort.dir;
  });
  return arr;
}

// A test's row state. Animations restart whenever the table re-renders, so their
// phase is pinned to the clock: a re-render mid-sweep continues where it was.
function testView(t) {
  const idle = { row: "", btn: "", style: "", label: "Test", title: "Test YouTube, Google, Instagram and Telegram now" };
  if (!t) return idle;
  const style = ` style="--phase:-${Date.now() % 1600}ms"`;
  if (t.state === "queued") return { row: " testing", btn: " testing", style, label: '<span class="spin"></span>Queued', title: "Waiting for the current check to finish" };
  if (t.state === "running") return { row: " testing", btn: " testing", style, label: '<span class="spin"></span>Testing', title: "Testing now" };
  if (t.ok) return { row: " tested-ok", btn: " pass", style: "", label: `✓ ${t.ms} ms`, title: "Passed the real-use check" };
  if (t.ok === false) return { row: " tested-bad", btn: " fail", style: "", label: "✗ Failed", title: "Failed the real-use check" };
  return t.offline
    ? { row: "", btn: " unknown", style: "", label: "! Offline", title: "Your own internet looked down, so this test was not counted" }
    : { row: "", btn: " unknown", style: "", label: "? No answer", title: "No answer in time; not counted as a failure" };
}
// ---------- site check ----------
let siteSel = null;   // host whose results are shown as a table column
const selectedSite = () => (S?.sitechecks || []).find((h) => h.host === siteSel) || null;
const sitesActive = () => (S?.sitechecks || []).some((h) => h.state !== "done");

function siteCell(n, site) {
  const ms = site.results[n.id];
  if (typeof ms === "number") return `<span class="reach ok">✓ ${ms} ms</span>`;
  if (ms === null) return `<span class="reach bad" title="Didn't answer through this config">✗</span>`;
  return site.state === "done" ? `<span class="reach na" title="Added after this check">–</span>`
    : `<span class="spin reach na" style="--phase:-${Date.now() % 1600}ms"></span>`;
}

// One tab per checked site (newest first). Only re-rendered when it changes, so hover
// and keyboard focus survive the polls.
function renderTabs() {
  const hist = S.sitechecks || [];
  if (siteSel && !selectedSite()) siteSel = null;
  const tab = (key, label, meta, x) => `<div class="tab${key === (siteSel || "") ? " sel" : ""}" role="tab" tabindex="0"
      aria-selected="${key === (siteSel || "")}" data-tab="${esc(key)}"><span class="tab-label">${label}</span>${meta}${x}</div>`;
  const html = [tab("", "All configs", `<span class="tab-n">${S.nodes.length}</span>`, "")].concat(hist.map((h) => {
    const done = Object.keys(h.results).length;
    const meta = h.state === "queued" ? '<span class="tab-n"><span class="spin"></span></span>'
      : h.state === "running" ? `<span class="tab-n"><span class="spin"></span>${done}/${h.total}</span>`
      : h.offline || h.interrupted ? `<span class="tab-n warn" title="${h.offline ? "Your internet was down" : "Interrupted"}">!</span>`
      : `<span class="tab-n" title="${h.reach} of ${h.total} configs reached it">${h.reach}/${h.total}</span>`;
    return tab(h.host, esc(h.host), meta, `<button class="tab-x" data-sact="forget" title="Forget this site" aria-label="Forget ${esc(h.host)}">×</button>`);
  })).join("");
  const el = $("#tabs");
  if (el.dataset.html !== html) { el.innerHTML = html; el.dataset.html = html; }

  const site = selectedSite();
  $("#node-table").classList.toggle("site-mode", !!site);
  if (site) { $("#site-col").textContent = site.host; $("#site-col").title = site.url; }
  const rerun = $("#site-rerun");
  rerun.hidden = !site;
  rerun.disabled = !!site && site.state !== "done";
  if (site) $("#balancer-hint").textContent = site.state === "done" ? `checked ${ago(site.at)}` : "checking…";
}

const testsActive = () => Object.values(S?.tests || {}).filter((t) => t.state !== "done").length;

function renderTestAll() {
  const all = Object.values(S.tests || {}), left = testsActive();
  const b = $("#test-all");
  b.classList.toggle("testing", left > 0);
  b.innerHTML = left > 0 && all.length > 1 ? `<span class="spin"></span>Testing ${all.length - left}/${all.length}` : "Test all";
}

// Removing takes a second click, so a stray click next to Share does nothing.
// The pending state lives here because the table re-renders on every poll.
let removeArm = { id: null, until: 0 };
function removeBtn(n) {
  return removeArm.id === n.id && Date.now() < removeArm.until
    ? `<button class="mini danger armed" data-act="remove" title="Click again to remove">?</button>`
    : `<button class="mini danger" data-act="remove" title="Remove this config">×</button>`;
}

function renderNodes() {
  renderTestAll();
  const site = selectedSite();
  $("#node-count").textContent = S.nodes.length;
  $("#nodes-empty").hidden = S.nodes.length > 0;
  $("#auto").hidden = !S.pinned;
  $("#balancer-hint").textContent = S.pinned ? "Pinned by you" : "Auto · best score, sticky";
  renderTabs();
  document.querySelectorAll("th[data-sort]").forEach((th) => th.classList.toggle("sorted", th.dataset.sort === sort.key));
  const maxPing = Math.max(1500, ...S.nodes.map((n) => n.ping_ms));
  const maxSpeed = Math.max(5000, ...S.nodes.map((n) => n.speed_kbps));
  $("#nodes").innerHTML = sortedNodes().map((n) => {
    const pclass = ["vless", "vmess", "trojan", "ss", "hysteria2"].includes(n.proto) ? `p-${n.proto}` : "p-other";
    const sp = fmtSpeed(n.speed_kbps);
    const w = Math.max(8, 100 - (n.ping_ms / maxPing) * 100);
    const name = n.name ? esc(n.name.slice(0, 60)) : esc(n.host);
    const rel = n.health ?? 0;
    const relColor = rel >= 80 ? "var(--accent)" : rel >= 50 ? "var(--amber)" : "var(--red)";
    const t = testView(S.tests?.[n.id]);
    return `<tr class="${n.active ? "active" : ""}${n.in_balancer ? "" : " out"}${n.eligible ? "" : " failing"}${site && site.results[n.id] === null ? " noreach" : ""}${t.row}" data-id="${n.id}"${t.style}${n.eligible && !n.active ? ' title="Double-click to use this config"' : ""}>
      <td class="rank">${n.rank}</td>
      <td><div class="loc"><span class="f">${n.flag}</span><div>${esc(regionName(n.country))}${n.active ? '<span class="live-dot" title="in use"></span>' : ""}${n.manual ? '<span class="tag-manual">manual</span>' : ""}<small title="${esc(n.name)}">${name}</small></div></div></td>
      <td><span class="badge ${pclass}">${esc(n.proto)}</span><span class="tsec" title="${esc(n.net)} · ${esc(n.sec)}">${esc(n.net)} · ${esc(n.sec)}</span></td>
      <td class="num c-site">${site ? siteCell(n, site) : ""}</td>
      <td class="num"><span class="health" title="${n.checks} checks · ${n.fails} failed in a row">${rel}%<span class="bar"><i style="width:${Math.max(4, rel)}%;background:${relColor}"></i></span></span></td>
      <td class="num c-ping"><span class="metric">${n.ping_ms > 0 ? n.ping_ms + " ms" : "–"}<span class="bar"><i style="width:${w}%;background:${pingColor(n.ping_ms)}"></i></span></span></td>
      <td class="num"><span class="metric${sp ? "" : " na"}">${sp || "–"}<span class="bar"><i style="width:${sp ? Math.max(8, (n.speed_kbps / maxSpeed) * 100) : 0}%;background:${speedColor(n.speed_kbps)}"></i></span></span></td>
      <td><div class="acts">
        ${n.eligible && !n.active ? `<button class="mini use" data-act="use">Use</button>` : ""}
        <button class="mini${t.btn}" data-act="test" title="${t.title}"${t.style}>${t.label}</button>
        <button class="mini icon" data-act="qr" title="Share: QR code and link" aria-label="Share">
          <svg viewBox="0 0 24 24"><circle cx="18" cy="5" r="3"/><circle cx="6" cy="12" r="3"/><circle cx="18" cy="19" r="3"/><path d="M8.6 13.5l6.8 4M15.4 6.5l-6.8 4"/></svg></button>
        ${removeBtn(n)}
      </div></td></tr>`;
  }).join("");
}

function renderSide() {
  const subUrl = S.lan.sub;
  if ($("#sub-url").textContent !== subUrl) {
    $("#sub-url").textContent = subUrl;
    $("#sub-qr").src = qrSrc(subUrl);
  }
  $("#lan-socks").textContent = S.lan.socks;
  $("#lan-http").textContent = S.lan.http;
  renderPhone();
  const t = S.tor;
  $("#tor-status").textContent = {
    missing: "Tor not installed",
    off: "Tor fallback on standby",
    starting: `Tor fallback starting… ${t.progress}%`,
    ready: "Tor fallback active",
  }[t.status] || "";
  const st = S.stats || {};
  const fn = st.fn ? `false negatives ~${Math.round((100 * st.fn.revived) / Math.max(st.fn.sampled, 1))}% (${st.fn.revived}/${st.fn.sampled} retired configs worked)` : "false-negative check runs hourly";
  $("#foot-stats").innerHTML = `<span>test traffic ~${st.bandwidth_mb_h ?? 0} MB/h</span><span>${fn}</span>`;
  const rep = Object.entries(S.report || {});
  $("#sources").innerHTML = rep.length
    ? rep.map(([u, r]) => `<li><span title="${esc(u)}">${esc(u.replace(/^https?:\/\//, ""))}</span><span><span class="hint">${r.count.toLocaleString()}</span> <span class="path ${r.path}">${r.path}</span></span></li>`).join("")
    : '<li><span>Waiting for the first fetch…</span></li>';
}

function renderPhone() {
  const ph = S.phone || {};
  const out = [];
  const ips = (S.lan.ips || []).filter((x) => x !== S.lan.ip);
  if (ips.length) out.push(`<div class="ps"><span>Other addresses of this PC: ${ips.map(esc).join(", ")}. Try one if the phone can't connect.</span></div>`);
  const port = new URL(S.lan.sub).port;
  out.push(`<div class="ps"><span>Phone check: open <b>http://${esc(S.lan.ip)}:${port}/ping</b> in the phone's browser</span></div>`);
  const ls = ph.last_sub, seen = $("#phone-seen");
  if (ls && ls.ip && !["127.0.0.1", "::1"].includes(ls.ip)) {
    seen.className = "phone-seen " + (ls.count ? "good" : "warn");
    seen.title = ls.ip;
    seen.textContent = ls.count ? `Phone synced ${ls.count} configs ${ago(ls.at)}` : `Phone reached Juggler ${ago(ls.at)}, no working configs yet`;
  } else {
    seen.className = "phone-seen";
    seen.title = "";
    seen.textContent = "Waiting for a phone";
  }
  const fw = ph.firewall || {};
  if (fw.supported) {
    if (fw.checking) out.push(`<div class="ps"><span>Checking Windows Firewall…</span></div>`);
    else if (fw.error) out.push(`<div class="ps"><span>Firewall check failed: ${esc(fw.error)}</span></div>`);
    else if (fw.ok) out.push(`<div class="ps good">✓ Firewall allows phones on this ${esc(fw.category)} network</div>`);
    else {
      const what = Object.entries(fw.detail || {}).filter(([, v]) => v !== "allowed").map(([k, v]) => `${k}: ${v}`).join(", ");
      out.push(`<div class="ps bad">✗ Windows Firewall blocks phones on this <b>${esc(fw.category)}</b> network <span>(${esc(what)})</span></div>`);
      out.push(`<button class="btn small" id="fw-allow">Allow phone access</button>`);
    }
  }
  const html = out.join("");
  const el = $("#phone-status");
  if (el.dataset.html !== html) { el.innerHTML = html; el.dataset.html = html; }
}

function render() {
  if (!S) return;
  renderHero(); renderControls(); renderNodes(); renderSide();
  if (!document.body.classList.contains("ready")) {
    void document.body.offsetWidth;   // flush the first real state without transitions…
    document.body.classList.add("ready");   // …then enable them for later changes
  }
}

// ---------- countdown ring ----------
function tick() {
  if (!S) return;
  const C = 351.9;                            // 2πr of the ring around the power button (r = 56)
  const left = Math.max(0, S.next_refresh - Date.now() / 1000 - (S._skew || 0));
  const busy = S.phase === "fetching";
  $("#countdown").textContent = busy ? "now" : `${String(Math.floor(left / 60)).padStart(2, "0")}:${String(Math.floor(left % 60)).padStart(2, "0")}`;
  const frac = busy || !S.next_refresh ? 1 : 1 - left / S.interval;
  $("#ring").style.strokeDashoffset = C * (1 - frac);
}
setInterval(tick, 1000);

// ---------- polling ----------
let fastPoll = null;
async function poll() {
  try {
    const s = await api("/api/state");
    s._skew = Date.now() / 1000 - s.now;
    S = s;
    render(); tick();
    clearTimeout(fastPoll);
    if (testsActive() || sitesActive()) fastPoll = setTimeout(poll, 1000);
  } catch { $("#subtitle").textContent = "dashboard lost connection to Juggler"; $("#orb").className = "orb off"; }
}
poll();
setInterval(poll, 4000);

// ---------- actions ----------
$("#refresh").onclick = async () => {
  try { const r = await api("/api/refresh", "POST"); toast(r.msg); setTimeout(poll, 400); } catch (e) { toast(e.message, true); }
};
$("#sysproxy").onclick = async () => {
  const b = $("#sysproxy");
  if (b.classList.contains("busy")) return;
  const on = b.getAttribute("aria-checked") !== "true";
  b.classList.add("busy");
  try { await api("/api/sysproxy", "POST", { on }); toast(on ? "Connected: Windows apps go through Juggler" : "Disconnected: Windows proxy restored"); await poll(); }
  catch (e) { toast(e.message, true); }
  finally { b.classList.remove("busy"); }
};
$("#safemode").onclick = async () => {
  const on = $("#safemode").getAttribute("aria-checked") !== "true";
  try { await api("/api/safemode", "POST", { on }); toast(on ? "Safe mode on" : "Safe mode off: unencrypted configs allowed from the next refresh"); poll(); } catch (e) { toast(e.message, true); }
};
$("#test-all").onclick = async () => {
  if (testsActive()) return;
  try { const r = await api("/api/test_all", "POST"); toast(r.msg); poll(); } catch (e) { toast(e.message, true); }
};
$("#add-open").onclick = () => { $("#add-modal").hidden = false; $("#add-text").focus(); };
$("#add-close").onclick = () => ($("#add-modal").hidden = true);
$("#add-save").onclick = async () => {
  try {
    const r = await api("/api/manual", "POST", { text: $("#add-text").value });
    toast(r.msg); $("#add-text").value = ""; $("#add-modal").hidden = true; setTimeout(poll, 6000);
  } catch (e) { toast(e.message, true); }
};
$("#auto").onclick = async () => { await api("/api/pin", "POST", { id: null }); toast("Back to automatic selection"); poll(); };

document.querySelectorAll("th[data-sort]").forEach((th) => (th.onclick = () => {
  const k = th.dataset.sort;
  sort = { key: k, dir: sort.key === k ? -sort.dir : 1 };
  renderNodes();
}));

async function useConfig(n) {
  try { await api("/api/pin", "POST", { id: n.id }); toast(`Using ${n.flag} ${regionName(n.country)}`); poll(); } catch (e) { toast(e.message, true); }
}

// Double-click or double-tap a row = Use. Counted by hand rather than with dblclick so
// touch screens get it too; rows re-render between taps, so the row is tracked by id.
let lastTap = { id: null, at: 0 };
function rowTap(ev) {
  const tr = ev.target.closest("tr[data-id]");
  if (!tr) return;
  const now = Date.now(), id = tr.dataset.id;
  const double = lastTap.id === id && now - lastTap.at < 400;
  lastTap = double ? { id: null, at: 0 } : { id, at: now };
  if (!double) return;
  const n = S.nodes.find((x) => x.id === id);
  if (!n || n.active) return;
  if (!n.eligible) { toast("This config isn't working right now", true); return; }
  useConfig(n);
}
$("#nodes").onmousedown = (ev) => {          // a double-click shouldn't select the row's text
  if (ev.detail > 1 && !ev.target.closest("button")) ev.preventDefault();
};

$("#nodes").onclick = async (ev) => {
  const btn = ev.target.closest("button[data-act]");
  if (!btn) { rowTap(ev); return; }
  const n = S.nodes.find((x) => x.id === btn.closest("tr").dataset.id);
  if (!n) return;
  if (btn.dataset.act === "copy") copy(n.link, "Config copied");
  if (btn.dataset.act === "qr") openModal(`${n.flag} ${regionName(n.country)} · ${n.proto}`, n.link);
  if (btn.dataset.act === "test") {
    if (S.tests?.[n.id] && S.tests[n.id].state !== "done") return;
    btn.classList.add("testing"); btn.innerHTML = '<span class="spin"></span>Queued';
    try { await api("/api/test", "POST", { id: n.id }); poll(); } catch (e) { toast(e.message, true); }
  }
  if (btn.dataset.act === "remove") {
    if (removeArm.id !== n.id || Date.now() >= removeArm.until) {
      removeArm = { id: n.id, until: Date.now() + 3000 };
      renderNodes();
      setTimeout(() => { if (removeArm.id === n.id) { removeArm = { id: null, until: 0 }; renderNodes(); } }, 3000);
      return;
    }
    removeArm = { id: null, until: 0 };
    try { await api("/api/remove", "POST", { id: n.id }); toast(`Removed ${n.flag} ${regionName(n.country)}`); poll(); } catch (e) { toast(e.message, true); }
  }
  if (btn.dataset.act === "use") useConfig(n);
};

document.addEventListener("click", async (ev) => {
  const b = ev.target.closest("button[data-copy]");
  if (b) copy($("#" + b.dataset.copy).textContent);
  if (ev.target.closest("#fw-allow")) {
    try { await api("/api/firewall/allow", "POST"); toast("Approve the Windows prompt; status updates in a few seconds"); setTimeout(poll, 6000); }
    catch (e) { toast(e.message, true); }
  }
});

// ---------- modal ----------
function openModal(title, link) {
  $("#modal-title").textContent = title;
  $("#modal-qr").src = qrSrc(link);
  $("#modal-link").textContent = link;
  $("#modal").hidden = false;
  $("#modal-copy").onclick = () => copy(link, "Config copied");
}
$("#modal-close").onclick = () => ($("#modal").hidden = true);
$("#modal").onclick = (e) => { if (e.target.id === "modal") $("#modal").hidden = true; };
document.addEventListener("keydown", (e) => { if (e.key === "Escape") { $("#modal").hidden = true; $("#add-modal").hidden = true; } });

// ---------- whitelist ----------
let WL = [];
function renderWL() {
  $("#wl").innerHTML = WL.map((e, i) => `<span class="chip">${esc(e)}<button title="Remove" data-i="${i}">×</button></span>`).join("");
}
async function saveWL(entries) {
  try { const r = await api("/api/whitelist", "PUT", { entries }); WL = r.entries; renderWL(); toast("Whitelist applied"); return true; }
  catch (e) { toast("Rejected: " + e.message, true); return false; }
}
$("#wl").onclick = (ev) => {
  const b = ev.target.closest("button[data-i]");
  if (b) saveWL(WL.filter((_, i) => i !== +b.dataset.i));
};
$("#wl-form").onsubmit = async (ev) => {
  ev.preventDefault();
  const v = $("#wl-input").value.trim();
  if (!v || WL.includes(v)) return;
  if (await saveWL([...WL, v])) $("#wl-input").value = "";
};
api("/api/whitelist").then((r) => { WL = r.entries; renderWL(); });

// ---------- sources & bridges ----------
for (const which of ["sources", "bridges"]) {
  api(`/api/${which}`).then((r) => ($(`#${which}-text`).value = r.lines.join("\n")));
  $(`#${which}-save`).onclick = async () => {
    const lines = $(`#${which}-text`).value.split("\n");
    try { await api(`/api/${which}`, "PUT", { lines }); toast(which === "bridges" ? "Bridges saved · Tor restarting" : "Sources saved · used from the next refresh"); }
    catch (e) { toast(e.message, true); }
  };
}

// ---------- live log ----------
const logEl = $("#log");
function logVisible(l) {
  if (logLevel === "all") return true;
  if (logLevel === "conn") return l.dataset.tag === "CONN";
  return l.dataset.level === logLevel;
}
function addLog(e) {
  const d = document.createElement("div");
  d.className = "l";
  d.dataset.level = e.level;
  const t = new Date(e.t * 1000).toLocaleTimeString([], { hour12: false });
  const sym = { ok: "✓", warn: "!", error: "✗", info: "•" }[e.level] || "•";
  d.innerHTML = `<span class="t">${t}</span><span class="tag tag-${esc(e.tag)}">${esc(e.tag)}</span><span class="s-${e.level}">${sym}</span><span>${esc(e.msg)}</span>`;
  d.dataset.tag = e.tag;
  d.hidden = !logVisible(d);
  const stick = logEl.scrollTop + logEl.clientHeight >= logEl.scrollHeight - 30;
  logEl.appendChild(d);
  while (logEl.childElementCount > 800) logEl.firstChild.remove();
  if (stick) logEl.scrollTop = logEl.scrollHeight;
  if (["ok", "error"].includes(e.level) && ["TEST", "JUGGLER", "XRAY"].includes(e.tag)) setTimeout(poll, 300);
}
function connectLog() {
  const es = new EventSource("/api/log/stream");
  let first = true;
  es.onopen = () => { if (!first) logEl.innerHTML = ""; first = false; };
  es.onmessage = (m) => addLog(JSON.parse(m.data));
  es.onerror = () => { es.close(); setTimeout(() => { logEl.innerHTML = ""; connectLog(); }, 3000); };
}
connectLog();
$("#filters").onclick = (ev) => {
  const b = ev.target.closest(".pill");
  if (!b) return;
  logLevel = b.dataset.level;
  document.querySelectorAll("#filters .pill").forEach((p) => p.classList.toggle("on", p === b));
  logEl.querySelectorAll(".l").forEach((l) => (l.hidden = !logVisible(l)));
};
$("#connlog").onclick = async () => {
  const on = !(S && S.connlog && S.connlog.on);
  try {
    await api("/api/connlog", "POST", { on });
    toast(on ? "Recording connections (Connections filter shows only those)" : "Stopped recording connections");
    poll();
  } catch (e) { toast(e.message, true); }
};

$("#site-form").onsubmit = async (ev) => {
  ev.preventDefault();
  const v = $("#site-input").value.trim();
  if (!v) return;
  try {
    const r = await api("/api/sitecheck", "POST", { url: v });
    $("#site-input").value = "";
    $("#site-input").blur();
    siteSel = r.host;
    sort = { key: "site", dir: 1 };
    poll();
  } catch (e) { toast(e.message, true); }
};
function openTab(host) {
  siteSel = host || null;
  sort = { key: siteSel ? "site" : "rank", dir: 1 };
  renderNodes();
}
$("#tabs").onclick = async (ev) => {
  const tab = ev.target.closest(".tab[data-tab]");
  if (!tab) return;
  const host = tab.dataset.tab;
  if (ev.target.closest("[data-sact=forget]")) {
    try { await api("/api/sitecheck", "DELETE", { host }); if (siteSel === host) openTab(null); poll(); } catch (e) { toast(e.message, true); }
    return;
  }
  openTab(host);
};
$("#tabs").onkeydown = (ev) => {
  const tab = ev.target.closest(".tab[data-tab]");
  if (tab && (ev.key === "Enter" || ev.key === " ")) { ev.preventDefault(); openTab(tab.dataset.tab); }
};
$("#site-rerun").onclick = async () => {
  const h = selectedSite();
  if (!h) return;
  try { await api("/api/sitecheck", "POST", { url: h.url }); poll(); } catch (e) { toast(e.message, true); }
};
$("#site-input").onfocus = (ev) => { ev.target.placeholder = "chatgpt.com  ↵"; };
$("#site-input").onblur = (ev) => { ev.target.placeholder = "+ Check a site"; };
