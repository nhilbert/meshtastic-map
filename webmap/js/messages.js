// Messaging pane under the map: the connected node's channels and direct conversations,
// incoming and outgoing texts with delivery state, and the recent packets ("Alle Pakete").
// The server keeps the messages (data/messages.jsonl); the page polls /api/messages for
// everything newer than the last revision it has. Meant for simple messaging while using the
// map; anything more is better done in a Meshtastic app.
import { locale, t } from "./i18n.js";
import { $, esc, getJSON, postJSON } from "./util.js";
import { setBadge, setRail } from "./workspace.js";

const MAX_BYTES = 200;  // same limit as the server (meshplay.mapapp.messages.MAX_TEXT_BYTES)
const TRAFFIC = "traffic";
const KEEP_TRAFFIC = 1000;  // as the server (meshplay.mapapp.messages.KEEP_TRAFFIC)
// Packet types for the filter of "Alle Pakete": the protocol's ports, grouped as they occur on
// a mesh. Every other port (admin, range test, store & forward, sensors, ATAK, private apps)
// is "other"; "ENCRYPTED" stands for a packet the node has no key for.
const KINDS = [
  ["text", ["TEXT_MESSAGE_APP", "TEXT_MESSAGE_COMPRESSED_APP", "ALERT_APP"]],
  ["position", ["POSITION_APP", "WAYPOINT_APP"]],
  ["nodeinfo", ["NODEINFO_APP", "NODE_STATUS_APP"]],
  ["telemetry", ["TELEMETRY_APP"]],
  ["routing", ["ROUTING_APP", "NEIGHBORINFO_APP"]],
  ["traceroute", ["TRACEROUTE_APP"]],
  ["encrypted", ["ENCRYPTED"]],
  ["other", []],
];
const LOCAL = "local";  // not a type: what the own node gives only to the app (any type)
const kindOf = p => (KINDS.find(([, ports]) => ports.includes(p.port)) || KINDS.at(-1))[0];
const kindLabels = () => ({
  text: t("Text"), position: t("Position"), nodeinfo: t("Knoteninfo"), telemetry: t("Telemetrie"),
  routing: t("Routing"), traceroute: t("Traceroute"), encrypted: t("verschlüsselt"),
  other: t("Sonstige"), [LOCAL]: t("nur App"),
});
const M = {
  api: null, open: false, conv: null, rev: 0, timer: null,
  msgs: new Map(), traffic: [], channels: [], me: null, state: "getrennt", names: {},
  seen: {}, extraDm: new Set(), sending: false, hide: new Set(),
};

// api: { store, toast(msg, opts), connect(), focusNode(id) }
export function initMessages(api) {
  M.api = api;
  M.open = api.store.get("msg.open", false);
  M.conv = api.store.get("msg.conv", null);
  M.seen = api.store.get("msg.seen", {}) || {};
  for (const id of api.store.get("msg.dms", []) || []) M.extraDm.add(id);
  for (const id of api.store.get("msg.hide", []) || []) M.hide.add(id);
  $("#btnMsg").addEventListener("click", () => setOpen(!M.open));
  $("#msgHead").addEventListener("click", e => { if (!e.target.closest("button, input, label")) setOpen(!M.open); });
  $("#msgFold").addEventListener("click", () => setOpen(!M.open));
  $("#msgConnect").addEventListener("click", () => api.connect());
  const txt = $("#msgText");
  txt.addEventListener("input", updateCount);
  txt.addEventListener("keydown", e => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); } });
  $("#msgSend").addEventListener("click", send);
  $("#msgList").addEventListener("click", e => {
    const node = e.target.closest("[data-node]");
    if (node) { api.focusNode(node.dataset.node); return; }
    const head = e.target.closest(".pkthd");
    if (head) togglePacket(head.parentElement);
  });
  $("#msgFilter").addEventListener("click", e => {
    const chip = e.target.closest("[data-kind]");
    if (!chip) return;
    if (!M.hide.delete(chip.dataset.kind)) M.hide.add(chip.dataset.kind);
    api.store.set("msg.hide", [...M.hide]);
    render();
  });
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
  if (open) { setRail(false, false); M.api.onOpen?.(); }
  M.open = open; M.api.store.set("msg.open", open);
  $("#msgpane").classList.toggle("open", open);
  $("#msgFold").textContent = open ? "▾" : "▴";
  $("#msgFold").setAttribute("aria-label", open ? t("Nachrichten einklappen") : t("Nachrichten ausklappen"));
  $("#msgFold").setAttribute("aria-expanded", String(open));
  $("#msgFold").setAttribute("aria-controls", "msgBody");
  $("#btnMsg").setAttribute("aria-pressed", String(open));
  $("#btnMsg").classList.toggle("on", open);
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
    M.traffic = M.traffic.concat(d.traffic).slice(-KEEP_TRAFFIC);
    Object.assign(M.names, d.names);
    M.rev = d.rev; M.channels = d.channels; M.me = d.me; M.state = d.state;
    if (M.open) markSeen();
    for (const m of fresh) if (m.to !== "^all" && !(M.open && convOf(m) === M.conv))
      M.api.toast(t("Direktnachricht von {name}: {text}", { name: nodeName(m.from), text: m.text }), { action: [t("Öffnen"), () => openConversation(convOf(m))] });
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
function chanName(c) { return `${c.index} · ${c.name || (c.primary ? t("Primär") : t("Kanal {n}", { n: c.index }))}`; }
function convLabel(key) {
  if (key === TRAFFIC) return t("Alle Pakete");
  if (key.startsWith("ch:")) {
    const c = M.channels.find(x => x.index === +key.slice(3));
    return c ? chanName(c) : t("Kanal {n}", { n: key.slice(3) });
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
  const title = total ? t("Nachrichten · {n} neu", { n: total }) : t("Nachrichten");
  setBadge("msg", total ? { num: total } : null, title);
  $("#msgTitle").textContent = title;
  renderHead();
  if (!M.open) return;
  const item = k => {
    const n = unread(k);
    return `<button class="conv ${k === M.conv ? "sel" : ""}" data-conv="${esc(k)}" title="${esc(convLabel(k))}">
      <span class="nm">${k.startsWith("dm:") ? "✉ " : "# "}${esc(convLabel(k))}</span>${n ? `<span class="badge">${n}</span>` : ""}</button>`;
  };
  $("#msgConvs").innerHTML = `<div class="grp">${t("Kanäle")}</div>${convs.filter(k => k.startsWith("ch:")).map(item).join("") || `<p class="note">${t("keine (Gerät verbinden)")}</p>`}
    <div class="grp">${t("Direkt")}</div>${convs.filter(k => k.startsWith("dm:")).map(item).join("") || `<p class="note">${t("Knoten über die Karte oder die Knotenliste anschreiben")}</p>`}
    <div class="grp">${t("Verkehr")}</div>${`<button class="conv ${M.conv === TRAFFIC ? "sel" : ""}" data-conv="${TRAFFIC}"><span class="nm">${t("Alle Pakete")}</span></button>`}`;
  $("#msgConvs").querySelectorAll("[data-conv]").forEach(b => b.addEventListener("click", () => select(b.dataset.conv)));
  const list = $("#msgList"), stick = list.scrollTop + list.clientHeight >= list.scrollHeight - 30;
  renderFilter();
  if (M.conv === TRAFFIC) {
    $("#msgTo").textContent = t("Alle empfangenen Pakete (neueste unten; nur solange die Karten-App läuft). Klick auf ein Paket zeigt seine Felder.");
    renderTraffic(list);
  } else {
    const direct = M.conv.startsWith("dm:");
    $("#msgTo").innerHTML = direct
      ? t("An {name} · direkt, über Kanal 0", { name: `<button class="lnk" data-node="${esc(M.conv.slice(3))}">${esc(convLabel(M.conv))}</button>` })
      : t("An alle auf Kanal {name}", { name: `<strong>${esc(convLabel(M.conv))}</strong>` });
    list.dataset.mode = "";
    list.innerHTML = messagesOf(M.conv).map(msgHTML).join("") || `<p class="note">${t("Noch keine Nachrichten.")}</p>`;
  }
  $("#msgTo").querySelectorAll("[data-node]").forEach(b => b.addEventListener("click", () => M.api.focusNode(b.dataset.node)));
  if (stick) list.scrollTop = list.scrollHeight;
  const can = M.state === "verbunden" && M.conv !== TRAFFIC;
  $("#msgCompose").hidden = M.conv === TRAFFIC;
  $("#msgText").disabled = !can || M.sending; $("#msgSend").disabled = !can || M.sending;
  $("#msgText").placeholder = M.state !== "verbunden" ? t("Gerät nicht verbunden")
    : t("Nachricht an {name} (Enter sendet, Umschalt+Enter neue Zeile)", { name: convLabel(M.conv) });
  updateCount();
}
function renderHead(err) {
  const ok = M.state === "verbunden";
  $("#msgState").textContent = err ? t("Nachrichten nicht abrufbar: {error}", { error: err })
    : ok ? t("verbunden als {name}", { name: M.me ? M.me.name || M.me.id : "?" })
      : M.state === "verbinde" ? t("verbinde …") : t("Gerät nicht verbunden");
  $("#msgState").classList.toggle("off", !ok);
  $("#msgConnect").hidden = ok || M.state === "verbinde";
}
const clock = ts => new Date(ts * 1000).toLocaleTimeString(locale, { hour: "2-digit", minute: "2-digit" });
const dayClock = ts => new Date(ts * 1000).toDateString() === new Date().toDateString() ? clock(ts)
  : new Date(ts * 1000).toLocaleString(locale, { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" });
const hops = n => (n === 1 ? t("1 Hop") : t("{n} Hops", { n }));
// Delivery states are stored as German codes; "nicht zugestellt (REASON)" keeps the firmware's reason.
// Codes: t("gesendet") t("zugestellt") t("im Netz")
function statusText(s) {
  const m = /^nicht zugestellt \((.+)\)$/.exec(s);
  return m ? t("nicht zugestellt ({reason})", { reason: m[1] }) : t(s);
}
function msgHTML(m) {
  const meta = m.dir === "in"
    ? `<button class="lnk" data-node="${esc(m.from)}">${esc(nodeName(m.from))}</button> · ${dayClock(m.time)}`
      + (m.snr != null ? ` · SNR ${m.snr} dB` : "") + (m.hops != null ? ` · ${hops(m.hops)}` : "")
    : `${dayClock(m.time)} · <span class="st ${m.status === "zugestellt" || m.status === "im Netz" ? "ok" : m.status.startsWith("nicht") ? "bad" : ""}">${esc(statusText(m.status))}</span>`;
  return `<div class="bubble ${m.dir}"><div class="t">${esc(m.text)}</div><div class="meta">${meta}</div></div>`;
}
// The type filter above the packet list: a switch per type with the number of its packets,
// then one for the packets the own node gives only to the app.
function renderFilter() {
  const bar = $("#msgFilter");
  bar.hidden = M.conv !== TRAFFIC;
  if (bar.hidden) return;
  if (!bar.children.length) {
    const labels = kindLabels(), why = esc(t("Vom eigenen Knoten nur an die App gegeben, nicht gefunkt"));
    bar.innerHTML = [...KINDS.map(([id]) => id), LOCAL].map(id =>
      `<button class="fchip${id === LOCAL ? ` loc" title="${why}` : ""}" data-kind="${id}">${esc(labels[id])} <span class="n"></span></button>`).join("");
  }
  const count = {};
  for (const p of M.traffic) {
    count[kindOf(p)] = (count[kindOf(p)] || 0) + 1;
    if (p.local) count[LOCAL] = (count[LOCAL] || 0) + 1;
  }
  for (const chip of bar.children) {
    chip.setAttribute("aria-pressed", String(!M.hide.has(chip.dataset.kind)));
    chip.querySelector(".n").textContent = count[chip.dataset.kind] || 0;
  }
}
// The packet list is updated row by row (new ones appended, dropped ones removed), so that an
// opened packet keeps its place and its text selection while more packets arrive; the filter
// only hides rows.
function renderTraffic(list) {
  if (list.dataset.mode !== TRAFFIC) { list.innerHTML = ""; list.dataset.mode = TRAFFIC; }
  const revs = new Set(M.traffic.map(p => p.rev));
  let last = 0, shown = 0;
  for (const row of list.querySelectorAll(".pkt")) {
    const rev = +row.dataset.pkt;
    if (revs.has(rev)) last = Math.max(last, rev); else row.remove();
  }
  for (const p of M.traffic) if (p.rev > last) list.insertAdjacentHTML("beforeend", trafficHTML(p));
  for (const row of list.querySelectorAll(".pkt")) {
    row.hidden = M.hide.has(row.dataset.kind) || (row.classList.contains(LOCAL) && M.hide.has(LOCAL));
    if (!row.hidden) shown++;
  }
  const note = !M.traffic.length ? t("Noch keine Pakete empfangen.") : shown ? "" : t("Kein Paket passt zum Filter.");
  const old = list.querySelector(".note");
  if ((old ? old.textContent : "") === note) return;
  old?.remove();
  if (note) list.insertAdjacentHTML("beforeend", `<p class="note">${esc(note)}</p>`);
}
function trafficHTML(p, open = false) {
  const to = p.to === "^all" ? t("alle") : nodeName(p.to);
  const port = p.port === "ENCRYPTED" ? t("verschlüsselt") : p.port.replace(/_APP$/, "").toLowerCase();
  const local = p.local ? ` <span class="loc" title="${esc(t("Vom eigenen Knoten nur an die App gegeben, nicht gefunkt"))}">${esc(t("nur App"))}</span>` : "";
  return `<div class="pkt${open ? " open" : ""}${p.local ? " " + LOCAL : ""}" data-pkt="${p.rev}" data-kind="${kindOf(p)}"><div class="pkthd">
    <button class="lnk fold" aria-expanded="${open}" aria-label="${esc(t("Felder des Pakets"))}" title="${esc(t("Felder des Pakets"))}"${p.packet ? "" : " disabled"}>${open ? "▾" : "▸"}</button>
    <span class="tm">${clock(p.time)}</span>
    <button class="lnk" data-node="${esc(p.from)}">${esc(nodeName(p.from))}</button> → ${esc(to)}
    <span class="port">${esc(port)}</span>${local} ${t("Kanal {n}", { n: p.channel })}${p.snr != null ? ` · SNR ${p.snr} dB` : ""}${p.hops != null ? ` · ${hops(p.hops)}` : ""}
    ${p.text ? `<span class="txt">${esc(t("„{text}“", { text: p.text }))}</span>` : ""}</div>${open ? packetHTML(p.packet) : ""}</div>`;
}
function togglePacket(row) {
  const p = M.traffic.find(x => x.rev === +row.dataset.pkt);
  if (!p || !p.packet) return;
  row.insertAdjacentHTML("afterend", trafficHTML(p, !row.classList.contains("open")));
  const fresh = row.nextElementSibling;
  row.remove();
  fresh.querySelector(".fold").focus();
  fresh.scrollIntoView({ block: "nearest" });
}
// Every field of a packet, the packet's own ones before the decoded content. The names are
// the protocol's; the payload bytes are left out where the fields say the same.
const DECODED_META = ["portnum", "payload", "bitfield", "wantResponse", "requestId", "replyId", "dest", "source"];
function packetFields(value, prefix = "") {
  if (value === null || typeof value !== "object") return [[prefix.slice(0, -1), value]];
  if (Array.isArray(value) && value.every(v => v === null || typeof v !== "object")) return [[prefix.slice(0, -1), value.join(", ")]];
  return Object.entries(value).flatMap(([k, v]) => packetFields(v, prefix + k + "."));
}
function packetHTML(packet) {
  const decoded = packet.decoded || {};
  const told = Object.keys(decoded).some(k => !DECODED_META.includes(k));
  const all = packetFields(packet).filter(([k]) => !(told && k === "decoded.payload"));
  const own = all.filter(([k]) => !k.startsWith("decoded."));
  const value = (k, v) => /time(stamp)?$/i.test(k) && typeof v === "number" && v > 1e9
    ? `${v} · ${new Date(v * 1000).toLocaleString(locale)}` : String(v);
  return `<dl class="pktdl">${own.concat(all.filter(f => !own.includes(f)))
    .map(([k, v]) => `<dt>${esc(k)}</dt><dd>${esc(value(k, v))}</dd>`).join("")}</dl>`;
}
function updateCount() {
  const n = new TextEncoder().encode($("#msgText").value.trim()).length;
  $("#msgBytes").textContent = t("{n}/{max} Bytes", { n, max: MAX_BYTES });
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
