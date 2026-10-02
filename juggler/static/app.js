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
  const n = S.nodes.find((x) => x.id === S.active) || (S.mode === "nodes" ? S.nodes.find((x) => x.in_balancer) : null);
  const orb = $("#orb");
  orb.className = "orb " + (S.mode === "nodes" ? "on" : S.mode === "tor" ? "tor" : "off");
  const eb = $("#hero-eyebrow");
  const chips = [];
  if (n) {
    $("#hero-flag").textContent = n.flag;
    eb.className = "eyebrow"; eb.textContent = S.pinned ? "Connected · pinned" : "Connected";
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
  chips.push(`<span class="chip indigo">balancer <b>${S.nodes.filter((x) => x.in_balancer).length}</b></span>`);
  chips.push(`<span class="chip">refreshed <b>${ago(S.last_refresh)}</b></span>`);
  $("#hero-chips").innerHTML = chips.join("");
  $("#subtitle").textContent = S.xray ? `socks :20808 · http :20809 · ${S.mode === "nodes" ? "online" : S.mode}` : "xray is not running";

  const phases = { fetching: "Fetching sources", testing: "Testing configs", speed: "Measuring speed", starting: "Starting" };
  const ph = $("#phase");
  ph.textContent = phases[S.phase] || "";
  ph.classList.toggle("show", !!phases[S.phase]);
  $("#refresh").disabled = S.phase !== "idle";
}

function renderControls() {
  const sp = $("#sysproxy");
  sp.setAttribute("aria-checked", S.sysproxy.on);
  sp.disabled = !S.sysproxy.supported;
  $("#ctl-sysproxy").classList.toggle("disabled", !S.sysproxy.supported);
  $("#sysproxy-desc").textContent = S.sysproxy.supported
    ? (S.sysproxy.on ? "Windows apps go through 127.0.0.1:20809" : "Route Windows apps through Juggler")
    : "Windows only. Point apps at 127.0.0.1:20808";
  $("#safemode").setAttribute("aria-checked", S.safe_mode);
  const st = S.stats || {};
  const steps = [["links", "links found"], ["fresh", "new unique"], ["valid", "tested"], ["alive", "alive"], [null, "in balancer"]];
  $("#funnel").innerHTML = steps.map(([k, label], i) => {
    const v = k ? st[k] ?? "–" : S.nodes.filter((x) => x.in_balancer).length;
    return `<div class="step${i >= 3 ? " final" : ""}"><b>${typeof v === "number" ? v.toLocaleString() : v}</b><span>${label}</span></div>`;
  }).join("");
}

function sortedNodes() {
  const arr = S.nodes.map((n, i) => ({ ...n, rank: i + 1 }));
  const k = sort.key;
  arr.sort((a, b) => {
    let x = a[k], y = b[k];
    if (k === "speed_kbps") { x = x > 0 ? -x : 0; y = y > 0 ? -y : 0; }   // fastest first
    if (k === "country") { x = regionName(x); y = regionName(y); }
    return (x > y ? 1 : x < y ? -1 : 0) * sort.dir;
  });
  return arr;
}

function renderNodes() {
  $("#node-count").textContent = S.nodes.length;
  $("#nodes-empty").hidden = S.nodes.length > 0;
  $("#auto").hidden = !S.pinned;
  $("#balancer-hint").textContent = S.pinned ? "Pinned manually" : "Auto · lowest ping wins";
  document.querySelectorAll("th[data-sort]").forEach((th) => th.classList.toggle("sorted", th.dataset.sort === sort.key));
  const maxPing = Math.max(1500, ...S.nodes.map((n) => n.ping_ms));
  $("#nodes").innerHTML = sortedNodes().map((n) => {
    const pclass = ["vless", "vmess", "trojan", "ss", "hysteria2"].includes(n.proto) ? `p-${n.proto}` : "p-other";
    const sp = fmtSpeed(n.speed_kbps);
    const w = Math.max(8, 100 - (n.ping_ms / maxPing) * 100);
    const name = n.name ? esc(n.name.slice(0, 60)) : esc(n.host);
    return `<tr class="${n.active ? "active" : ""}${n.in_balancer ? "" : " out"}" data-id="${n.id}">
      <td class="rank">${n.rank}</td>
      <td><div class="loc"><span class="f">${n.flag}</span><div>${esc(regionName(n.country))}${n.active ? '<span class="live-dot" title="in use"></span>' : ""}<small title="${esc(n.name)}">${name}</small></div></div></td>
      <td><span class="badge ${pclass}">${esc(n.proto)}</span><span class="tsec" title="${esc(n.net)} · ${esc(n.sec)}">${esc(n.net)} · ${esc(n.sec)}</span></td>
      <td class="num"><span class="ping">${n.ping_ms} ms<span class="bar"><i style="width:${w}%;background:${pingColor(n.ping_ms)}"></i></span></span></td>
      <td class="num">${sp ? `<span class="speed">${sp}</span>` : '<span class="speed na">n/a</span>'}</td>
      <td><div class="acts">
        ${n.in_balancer && !n.active ? `<button class="mini use" data-act="use">Use</button>` : ""}
        <button class="mini" data-act="copy">Copy</button>
        <button class="mini" data-act="qr">QR</button>
      </div></td></tr>`;
  }).join("");
}

function renderSide() {
  const subUrl = S.lan.sub;
  if ($("#sub-url").textContent !== subUrl) {
    $("#sub-url").textContent = subUrl;
    $("#sub-qr").src = qrSrc(subUrl);
  }
  $("#lan-socks").textContent = `SOCKS ${S.lan.socks}\nHTTP  ${S.lan.http}`;
  const t = S.tor;
  $("#tor-status").textContent = !t.installed ? "Tor not installed" : t.ready ? `Tor ready · ${t.bridges} bridge(s)` : `Tor ${t.progress}%`;
  const rep = Object.entries(S.report || {});
  $("#sources").innerHTML = rep.length
    ? rep.map(([u, r]) => `<li><span title="${esc(u)}">${esc(u.replace(/^https?:\/\//, ""))}</span><span><span class="hint">${r.count.toLocaleString()}</span> <span class="path ${r.path}">${r.path}</span></span></li>`).join("")
    : '<li><span>Waiting for the first fetch…</span></li>';
}

function render() {
  if (!S) return;
  renderHero(); renderControls(); renderNodes(); renderSide();
}

// ---------- countdown ring ----------
function tick() {
  if (!S) return;
  const C = 326.7;
  const left = Math.max(0, S.next_refresh - Date.now() / 1000 - (S._skew || 0));
  const busy = S.phase !== "idle";
  $("#countdown").textContent = busy ? "now" : `${String(Math.floor(left / 60)).padStart(2, "0")}:${String(Math.floor(left % 60)).padStart(2, "0")}`;
  const frac = busy || !S.next_refresh ? 1 : 1 - left / S.interval;
  $("#ring").style.strokeDashoffset = C * (1 - frac);
}
setInterval(tick, 1000);

// ---------- polling ----------
async function poll() {
  try {
    const s = await api("/api/state");
    s._skew = Date.now() / 1000 - s.now;
    S = s;
    render(); tick();
  } catch { $("#subtitle").textContent = "dashboard lost connection to Juggler"; $("#orb").className = "orb off"; }
}
poll();
setInterval(poll, 4000);

// ---------- actions ----------
$("#refresh").onclick = async () => {
  try { const r = await api("/api/refresh", "POST"); toast(r.msg); setTimeout(poll, 400); } catch (e) { toast(e.message, true); }
};
$("#sysproxy").onclick = async () => {
  const on = $("#sysproxy").getAttribute("aria-checked") !== "true";
  try { await api("/api/sysproxy", "POST", { on }); toast(on ? "System proxy on" : "System proxy restored"); poll(); } catch (e) { toast(e.message, true); }
};
$("#safemode").onclick = async () => {
  const on = $("#safemode").getAttribute("aria-checked") !== "true";
  try { await api("/api/safemode", "POST", { on }); toast(on ? "Safe mode on" : "Safe mode off: unencrypted configs allowed from the next refresh"); poll(); } catch (e) { toast(e.message, true); }
};
$("#auto").onclick = async () => { await api("/api/pin", "POST", { id: null }); toast("Back to automatic selection"); poll(); };

document.querySelectorAll("th[data-sort]").forEach((th) => (th.onclick = () => {
  const k = th.dataset.sort;
  sort = { key: k, dir: sort.key === k ? -sort.dir : 1 };
  renderNodes();
}));

$("#nodes").onclick = async (ev) => {
  const btn = ev.target.closest("button[data-act]");
  if (!btn) return;
  const n = S.nodes.find((x) => x.id === btn.closest("tr").dataset.id);
  if (!n) return;
  if (btn.dataset.act === "copy") copy(n.link, "Config copied");
  if (btn.dataset.act === "qr") openModal(`${n.flag} ${regionName(n.country)} · ${n.proto}`, n.link);
  if (btn.dataset.act === "use") {
    try { await api("/api/pin", "POST", { id: n.id }); toast(`Using ${n.flag} ${regionName(n.country)}`); poll(); } catch (e) { toast(e.message, true); }
  }
};

document.addEventListener("click", (ev) => {
  const b = ev.target.closest("button[data-copy]");
  if (b) copy($("#" + b.dataset.copy).textContent);
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
document.addEventListener("keydown", (e) => { if (e.key === "Escape") $("#modal").hidden = true; });

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
function addLog(e) {
  const d = document.createElement("div");
  d.className = "l";
  d.dataset.level = e.level;
  const t = new Date(e.t * 1000).toLocaleTimeString([], { hour12: false });
  const sym = { ok: "✓", warn: "!", error: "✗", info: "•" }[e.level] || "•";
  d.innerHTML = `<span class="t">${t}</span><span class="tag tag-${esc(e.tag)}">${esc(e.tag)}</span><span class="s-${e.level}">${sym}</span><span>${esc(e.msg)}</span>`;
  if (logLevel !== "all" && e.level !== logLevel) d.hidden = true;
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
  logEl.querySelectorAll(".l").forEach((l) => (l.hidden = logLevel !== "all" && l.dataset.level !== logLevel));
};
