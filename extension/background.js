// Juggler companion: points Chrome's proxy at the local Juggler and keeps the badge honest.
// The extension can't run Xray itself (browsers only speak HTTP/SOCKS proxies), so Juggler
// must be running on this PC. Whitelisted sites (.ir, LAN) bypass the proxy, as in Juggler.

const API = "http://127.0.0.1:8765";
const PROXY = { scheme: "http", host: "127.0.0.1", port: 20809 };

async function getJSON(path, timeoutMs = 3000) {
  const ctl = new AbortController();
  const t = setTimeout(() => ctl.abort(), timeoutMs);
  try {
    const r = await fetch(API + path, { signal: ctl.signal, cache: "no-store" });
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    return await r.json();
  } finally {
    clearTimeout(t);
  }
}

// Juggler whitelist entries -> Chrome bypass rules (same idea as sysproxy.bypass_list).
function bypassRules(entries) {
  const out = ["<local>", "localhost", "127.0.0.1", "[::1]"];
  for (let e of entries || []) {
    e = e.trim();
    if (["geosite:ir", "geosite:category-ir", "regexp:\\.ir$", "domain:ir", ".ir", "ir"].includes(e)) out.push("*.ir");
    else if (e === "geoip:private") out.push("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "169.254.0.0/16");
    else if (/^(geosite|geoip|regexp|keyword):/.test(e)) continue;      // not expressible in Chrome
    else if (e.startsWith("full:")) out.push(e.slice(5));
    else if (/^[0-9a-fA-F:.]+\/\d+$/.test(e)) out.push(e);                // CIDR
    else {
      const d = e.replace(/^domain:/, "").replace(/^[*.]+/, "");
      if (d) out.push(d, "*." + d);
    }
  }
  return [...new Set(out)];
}

async function applyProxy(on) {
  if (!on) {
    await chrome.proxy.settings.clear({ scope: "regular" });
    return { ok: true };
  }
  let entries = [];
  try {
    entries = (await getJSON("/api/whitelist")).entries;
  } catch {
    // Juggler not reachable: still enable with the safe defaults; the badge will show "!"
  }
  await chrome.proxy.settings.set({
    scope: "regular",
    value: { mode: "fixed_servers", rules: { singleProxy: PROXY, bypassList: bypassRules(entries) } },
  });
  const cur = await chrome.proxy.settings.get({});
  return { ok: cur.levelOfControl === "controlled_by_this_extension", level: cur.levelOfControl };
}

async function isEnabled() {
  return (await chrome.storage.local.get({ enabled: false })).enabled;
}

async function refreshBadge() {
  const enabled = await isEnabled();
  let online = false;
  try {
    online = !!(await getJSON("/api/state", 2500)).xray;
  } catch {}
  if (!enabled) {
    await chrome.action.setBadgeText({ text: "" });
  } else if (online) {
    await chrome.action.setBadgeText({ text: "ON" });
    await chrome.action.setBadgeBackgroundColor({ color: "#059669" });
  } else {
    await chrome.action.setBadgeText({ text: "!" });
    await chrome.action.setBadgeBackgroundColor({ color: "#dc2626" });
  }
  await chrome.action.setTitle({
    title: !enabled ? "Juggler: Chrome not routed" : online ? "Juggler: Chrome is routed through Juggler"
      : "Juggler is not running. Start it, or turn routing off",
  });
  return { enabled, online };
}

async function restore() {
  if (await isEnabled()) await applyProxy(true);
  await refreshBadge();
}

chrome.runtime.onInstalled.addListener(restore);
chrome.runtime.onStartup.addListener(restore);
chrome.alarms.create("badge", { periodInMinutes: 1 });
chrome.alarms.onAlarm.addListener((a) => { if (a.name === "badge") refreshBadge(); });
chrome.proxy.onProxyError.addListener(() => refreshBadge());

chrome.runtime.onMessage.addListener((msg, _sender, reply) => {
  (async () => {
    if (msg.type === "setEnabled") {
      await chrome.storage.local.set({ enabled: !!msg.on });
      const res = await applyProxy(!!msg.on);
      reply({ ...res, ...(await refreshBadge()) });
    } else if (msg.type === "status") {
      const cur = await chrome.proxy.settings.get({});
      reply({ ...(await refreshBadge()), level: cur.levelOfControl });
    }
  })();
  return true; // async reply
});
