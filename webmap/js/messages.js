// Messaging pane under the map: the connected node's channels and direct conversations,
// incoming and outgoing texts with delivery state, and the recent packets ("Alle Pakete").
// The server keeps the messages (data/messages.jsonl); the page polls /api/messages for
// everything newer than the last revision it has. Meant for simple messaging while using the
// map; anything more is better done in a Meshtastic app.
import { $, esc, getJSON, postJSON } from "./util.js";

const MAX_BYTES = 200;  // same limit as the server (meshplay.mapapp.messages.MAX_TEXT_BYTES)
const TRAFFIC = "traffic";
const M = {
  api: null, open: false, conv: null, rev: 0, timer: null,
  msgs: new Map(), traffic: [], channels: [], me: null, state: "getrennt", names: {},
  seen: {}, extraDm: new Set(), sending: false,
};

// api: { store, toast(msg, opts), connect(), focusNode(id) }
export function initMessages(api) {
  M.api = api;
  M.open = api.store.get("msg.open", false);
  M.conv = api.store.get("msg.conv", null);
  M.seen = api.store.get("msg.seen", {}) || {};
  for (const id of api.store.get("msg.dms", []) || []) M.extraDm.add(id);
  $("#btnMsg").addEventListener("click", () => setOpen(!M.open));
  $("#msgHead").addEventListener("click", e => { if (!e.target.closest("button, input, label")) setOpen(!M.open); });
  $("#msgFold").addEventListener("click", () => setOpen(!M.open));
  $("#msgConnect").addEventListener("click", () => api.connect());
  const txt = $("#msgText");
  txt.addEventListener("input", updateCount);
  txt.addEventListener("keydown", e => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); } });
  $("#msgSend").addEventListener("click", send);
  setOpen(M.open);
  poll();
}

// Open the pane on a conversation: "ch:<index>" for a channel, "dm:!abcd1234" for a node.
export function openConversation(key) {
  if (key.startsWith("dm:")) { M.extraDm.add(key.slice(3)); M.api.store.set("msg.dms", [...M.extraDm]); }
  select(key); setOpen(true);
  $("#msgText").focus();
}

function setOpen(open) {
  M.open = open; M.api.store.set("msg.open", open);
  $("#msgpane").classList.toggle("open", open);
  $("#msgFold").textContent = open ? "▾" : "▴";
  $("#msgFold").setAttribute("aria-label", open ? "Nachrichten einklappen" : "Nachrichten ausklappen");
  $("#btnMsg").setAttribute("aria-pressed", String(open));
  if (open) markSeen();
  render();
}
function select(key) {
  M.conv = key; M.api.store.set("msg.conv", key);
  $("#msgErr").textContent = "";
  markSeen(); render();
}

// ---------------------------------------------------------------- data
async function poll() {
  clearTimeout(M.timer);
  try {
    const d = await getJSON(`api/messages?rev=${M.rev}&traffic=1`);
    const fresh = [];
    for (const m of d.messages) {
      const key = `${m.dir}:${m.id}:${m.time}`;
      if (!M.msgs.has(key) && m.dir === "in" && M.rev) fresh.push(m);
      M.msgs.set(key, m);
    }
    M.traffic = M.traffic.concat(d.traffic).slice(-200);
    Object.assign(M.names, d.names);
    M.rev = d.rev; M.channels = d.channels; M.me = d.me; M.state = d.state;
    if (M.open) markSeen();
    for (const m of fresh) if (m.to !== "^all" && !(M.open && convOf(m) === M.conv))
      M.api.toast(`Direktnachricht von ${nodeName(m.from)}: ${m.text}`, { action: ["Öffnen", () => openConversation(convOf(m))] });
    render();
  } catch (e) {
    M.state = "Fehler"; renderHead(e.message);
  }
  M.timer = setTimeout(poll, M.open ? 3000 : 6000);
}

const isDirect = m => m.to !== "^all";
function convOf(m) {
  if (!isDirect(m)) return "ch:" + m.channel;
  return "dm:" + (m.dir === "in" ? m.from : m.to);
}
function nodeName(id, long = false) {
  const n = M.names[id];
  if (!n) return id;
  return long && n.long ? `${n.long} (${n.short || id})` : n.short || n.long || id;
}
function chanName(c) { return `${c.index} · ${c.name || (c.primary ? "Primär" : "Kanal " + c.index)}`; }
function convLabel(key) {
  if (key === TRAFFIC) return "Alle Pakete";
  if (key.startsWith("ch:")) {
    const c = M.channels.find(x => x.index === +key.slice(3));
    return c ? chanName(c) : `Kanal ${key.slice(3)}`;
  }
  return nodeName(key.slice(3), true);
}
function messagesOf(key) {
  return [...M.msgs.values()].filter(m => convOf(m) === key).sort((a, b) => a.time - b.time);
}
function unread(key) {
  const seen = M.seen[key] || 0;
  return messagesOf(key).filter(m => m.dir === "in" && m.time > seen).length;
}
function markSeen() {
  if (!M.open || !M.conv || M.conv === TRAFFIC) return;
  const last = messagesOf(M.conv).at(-1);
  if (last && (M.seen[M.conv] || 0) < last.time) { M.seen[M.conv] = last.time; M.api.store.set("msg.seen", M.seen); }
}
// All conversations: the device's channels, then direct ones by latest message.
function conversations() {
  const chans = M.channels.map(c => "ch:" + c.index);
  for (const m of M.msgs.values()) if (!isDirect(m) && !chans.includes(convOf(m))) chans.push(convOf(m));
  const dms = new Map([...M.extraDm].map(id => ["dm:" + id, 0]));
  for (const m of M.msgs.values()) if (isDirect(m)) dms.set(convOf(m), Math.max(dms.get(convOf(m)) || 0, m.time));
  return chans.concat([...dms.entries()].sort((a, b) => b[1] - a[1]).map(([k]) => k));
}

// ---------------------------------------------------------------- view
function render() {
  const convs = conversations();
  if (!M.conv || (M.conv !== TRAFFIC && !convs.includes(M.conv))) M.conv = convs.find(k => k === "ch:1") || convs[0] || TRAFFIC;
  const total = convs.reduce((n, k) => n + unread(k), 0);
  $("#btnMsg").textContent = total ? `Nachrichten · ${total} neu` : "Nachrichten";
  $("#msgTitle").textContent = total ? `Nachrichten · ${total} neu` : "Nachrichten";
  $("#btnMsg").classList.toggle("busy", total > 0);
  renderHead();
  if (!M.open) return;
  const item = k => {
    const n = unread(k);
    return `<button class="conv ${k === M.conv ? "sel" : ""}" data-conv="${esc(k)}" title="${esc(convLabel(k))}">
      <span class="nm">${k.startsWith("dm:") ? "✉ " : "# "}${esc(convLabel(k))}</span>${n ? `<span class="badge">${n}</span>` : ""}</button>`;
  };
  $("#msgConvs").innerHTML = `<div class="grp">Kanäle</div>${convs.filter(k => k.startsWith("ch:")).map(item).join("") || `<p class="note">keine (Gerät verbinden)</p>`}
    <div class="grp">Direkt</div>${convs.filter(k => k.startsWith("dm:")).map(item).join("") || `<p class="note">Knoten über die Karte oder die Knotenliste anschreiben</p>`}
    <div class="grp">Verkehr</div>${`<button class="conv ${M.conv === TRAFFIC ? "sel" : ""}" data-conv="${TRAFFIC}"><span class="nm">Alle Pakete</span></button>`}`;
  $("#msgConvs").querySelectorAll("[data-conv]").forEach(b => b.addEventListener("click", () => select(b.dataset.conv)));
  const list = $("#msgList"), stick = list.scrollTop + list.clientHeight >= list.scrollHeight - 30;
  if (M.conv === TRAFFIC) {
    $("#msgTo").innerHTML = "Alle empfangenen Pakete (neueste unten; nur solange die Karten-App läuft)";
    list.innerHTML = M.traffic.map(trafficHTML).join("") || `<p class="note">Noch keine Pakete empfangen.</p>`;
  } else {
    const direct = M.conv.startsWith("dm:");
    $("#msgTo").innerHTML = direct
      ? `An <button class="lnk" data-node="${esc(M.conv.slice(3))}">${esc(convLabel(M.conv))}</button> · direkt, über Kanal 0`
      : `An alle auf Kanal <strong>${esc(convLabel(M.conv))}</strong>`;
    list.innerHTML = messagesOf(M.conv).map(msgHTML).join("") || `<p class="note">Noch keine Nachrichten.</p>`;
  }
  list.querySelectorAll("[data-node]").forEach(b => b.addEventListener("click", () => M.api.focusNode(b.dataset.node)));
  $("#msgTo").querySelectorAll("[data-node]").forEach(b => b.addEventListener("click", () => M.api.focusNode(b.dataset.node)));
  if (stick) list.scrollTop = list.scrollHeight;
  const can = M.state === "verbunden" && M.conv !== TRAFFIC;
  $("#msgCompose").hidden = M.conv === TRAFFIC;
  $("#msgText").disabled = !can || M.sending; $("#msgSend").disabled = !can || M.sending;
  $("#msgText").placeholder = M.state !== "verbunden" ? "Gerät nicht verbunden"
    : `Nachricht an ${convLabel(M.conv)} (Enter sendet, Umschalt+Enter neue Zeile)`;
  updateCount();
}
function renderHead(err) {
  const ok = M.state === "verbunden";
  $("#msgState").textContent = err ? `Nachrichten nicht abrufbar: ${err}`
    : ok ? `verbunden als ${M.me ? M.me.name || M.me.id : "?"}` : M.state === "verbinde" ? "verbinde …" : "Gerät nicht verbunden";
  $("#msgState").classList.toggle("off", !ok);
  $("#msgConnect").hidden = ok || M.state === "verbinde";
}
const clock = t => new Date(t * 1000).toLocaleTimeString("de-DE", { hour: "2-digit", minute: "2-digit" });
const dayClock = t => new Date(t * 1000).toDateString() === new Date().toDateString() ? clock(t)
  : new Date(t * 1000).toLocaleString("de-DE", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" });
function msgHTML(m) {
  const meta = m.dir === "in"
    ? `<button class="lnk" data-node="${esc(m.from)}">${esc(nodeName(m.from))}</button> · ${dayClock(m.time)}`
      + (m.snr != null ? ` · SNR ${m.snr} dB` : "") + (m.hops != null ? ` · ${m.hops} Hop${m.hops === 1 ? "" : "s"}` : "")
    : `${dayClock(m.time)} · <span class="st ${m.status === "zugestellt" || m.status === "im Netz" ? "ok" : m.status.startsWith("nicht") ? "bad" : ""}">${esc(m.status)}</span>`;
  return `<div class="bubble ${m.dir}"><div class="t">${esc(m.text)}</div><div class="meta">${meta}</div></div>`;
}
function trafficHTML(p) {
  const to = p.to === "^all" ? "alle" : nodeName(p.to);
  const port = p.port.replace(/_APP$/, "").toLowerCase();
  return `<div class="pkt"><span class="tm">${clock(p.time)}</span>
    <button class="lnk" data-node="${esc(p.from)}">${esc(nodeName(p.from))}</button> → ${esc(to)}
    <span class="port">${esc(port)}</span> Kanal ${p.channel}${p.snr != null ? ` · SNR ${p.snr} dB` : ""}${p.hops != null ? ` · ${p.hops} Hops` : ""}
    ${p.text ? `<span class="txt">„${esc(p.text)}“</span>` : ""}</div>`;
}
function updateCount() {
  const n = new TextEncoder().encode($("#msgText").value.trim()).length;
  $("#msgBytes").textContent = `${n}/${MAX_BYTES} Bytes`;
  $("#msgBytes").classList.toggle("over", n > MAX_BYTES);
}

async function send() {
  const text = $("#msgText").value.trim();
  if (!text || M.sending || M.conv === TRAFFIC) return;
  const direct = M.conv.startsWith("dm:");
  M.sending = true; $("#msgErr").textContent = ""; render();
  try {
    await postJSON("api/messages", direct ? { text, to: M.conv.slice(3), channel: 0 }
      : { text, to: "^all", channel: +M.conv.slice(3) });
    $("#msgText").value = "";
  } catch (e) {
    $("#msgErr").textContent = e.message;
  } finally {
    M.sending = false; clearTimeout(M.timer); await poll();
    $("#msgText").focus();
  }
}
