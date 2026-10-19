"use strict";
const API = "http://127.0.0.1:8765";
const $ = (s) => document.querySelector(s);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const region = (() => { try { const d = new Intl.DisplayNames(["en"], { type: "region" }); return (c) => { try { return d.of(c); } catch { return c; } }; } catch { return (c) => c; } })();
const speed = (k) => (k > 0 ? (k >= 1000 ? (k / 1000).toFixed(1) + " Mbps" : k + " kbps") : "");
const send = (msg) => new Promise((res) => chrome.runtime.sendMessage(msg, res));

async function api(path, method = "GET", body) {
  const r = await fetch(API + path, { method, cache: "no-store",
    headers: body ? { "Content-Type": "application/json" } : {}, body: body ? JSON.stringify(body) : undefined });
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  return r.json();
}

function render(S, st) {
  $("#route").setAttribute("aria-checked", String(!!st.enabled));
  if (st.level && !["controlled_by_this_extension", "controllable_by_this_extension"].includes(st.level))
    $("#toggle-desc").textContent = "Another extension controls Chrome's proxy. Disable it to use Juggler.";
  $("#offline").hidden = !!S;
  for (const id of ["#hero", "#list-wrap", "#foot"]) $(id).hidden = !S;
  if (!S) { $("#sub").textContent = "not running"; return; }

  $("#sub").textContent = st.enabled ? (S.mode === "nodes" ? "Chrome is routed through Juggler" : "routed, waiting for configs")
                                      : "Chrome not routed";
  const n = S.nodes.find((x) => x.id === S.active) || S.nodes.find((x) => x.in_balancer);
  if (n) {
    $("#flag").textContent = n.flag;
    $("#eyebrow").textContent = S.pinned ? "Connected · pinned" : "Connected";
    $("#title").textContent = `${region(n.country)} · ${n.proto.toUpperCase()}`;
    $("#chips").innerHTML = `<span class="chip"><b>${n.ping_ms} ms</b></span>` +
      (speed(n.speed_kbps) ? `<span class="chip"><b>${speed(n.speed_kbps)}</b></span>` : "") +
      `<span class="chip">${esc(n.net)} · ${esc(n.sec)}</span>`;
  } else {
    $("#flag").textContent = S.mode === "tor" ? "🧅" : "⏳";
    $("#eyebrow").textContent = S.mode === "tor" ? "Fallback" : "Searching";
    $("#title").textContent = S.mode === "tor" ? "Tor (no configs yet)" : "Looking for configs…";
    $("#chips").innerHTML = "";
  }
  $("#auto").hidden = !S.pinned;
  $("#list").innerHTML = S.nodes.filter((x) => x.in_balancer).slice(0, 6).map((x) => `
    <li class="${x.id === S.active ? "active" : ""}" data-id="${x.id}">
      <span class="f">${x.flag}</span>
      <span class="n">${esc(region(x.country))}<small>${esc(x.proto)} · ${esc(x.net)} · ${esc(x.sec)}</small></span>
      <span class="p">${x.ping_ms} ms<br>${speed(x.speed_kbps)}</span>
      ${x.id === S.active ? '<span class="live">in use</span>' : '<button class="use">Use</button>'}
    </li>`).join("");
  const st2 = S.stats || {};
  $("#stats").textContent = `${st2.alive ?? S.nodes.length} working · ${S.phase === "idle" ? "idle" : S.phase}`;
  $("#refresh").disabled = S.phase !== "idle";
}

async function update() {
  const st = await send({ type: "status" });
  let S = null;
  try { S = await api("/api/state"); } catch {}
  render(S, st || {});
  if (!document.body.classList.contains("ready")) { void document.body.offsetWidth; document.body.classList.add("ready"); }
}

$("#route").onclick = async () => {
  const on = $("#route").getAttribute("aria-checked") !== "true";
  await send({ type: "setEnabled", on });
  update();
};
$("#dash").onclick = () => chrome.tabs.create({ url: "http://localhost:8765/" });
$("#refresh").onclick = async () => { try { await api("/api/refresh", "POST"); } catch {} update(); };
$("#auto").onclick = async () => { try { await api("/api/pin", "POST", { id: null }); } catch {} update(); };
$("#list").onclick = async (ev) => {
  const li = ev.target.closest("button.use") && ev.target.closest("li");
  if (!li) return;
  try { await api("/api/pin", "POST", { id: li.dataset.id }); } catch {}
  update();
};
update();
setInterval(update, 3000);
