"""Passive walk: what the device carried along heard, from the Android app's packet export."""

import bisect
from collections import Counter
from datetime import datetime
from pathlib import Path

from meshplay.applog import MAX_RELAY_KM, hops, node_id, relay_candidates, relay_grade
from meshplay.mapapp.heard_store import heard_dir, list_walks
from meshplay.mapapp.i18n import N_, _
from meshplay.mapapp.registry import Context, Layer, Setting, collection, feature, line
from meshplay.mapapp.style import GREY, RED, SNR_BANDS, hex_style, snr_color
from meshplay.walk import (
    distance_m,
    heard_points,
    heard_windows,
    load_gpx,
    load_heard,
    position_at,
)

# the track stays in the background so the markers stand out
TRACK_STYLE = {
    "rx": {"color": "#1a9850", "weight": 3, "opacity": 0.55},
    "quiet": {"color": RED, "weight": 3, "opacity": 0.55},
    "none": {"color": GREY, "weight": 3, "opacity": 0.55, "dash": "4 6"},
}
TRACK_LABEL = {  # German source texts
    "rx": N_("Empfang"),
    "quiet": N_("Gerät aktiv, nichts gehört"),
    "none": N_("keine Daten"),
}
GRADE_LABEL = {
    "unique": N_("eindeutig"),
    "likely": N_("wahrscheinlich"),
    "ambiguous": N_("mehrdeutig"),
    "unknown": N_("unbekannt"),
}
# categorical colours for the last hop, by frequency; the rest grey
RELAY_COLORS = [
    "#1f77b4",
    "#ff7f0e",
    "#2ca02c",
    "#d62728",
    "#9467bd",
    "#8c564b",
    "#e377c2",
    "#17becf",
]
CANDIDATE_COLOR = "#00b3b3"
STACK_M = 15.0  # packets closer than this to each other are drawn as one marker with a count


def relay_key(p: dict) -> str:
    """ "direct", the relay byte in hex ("14"), or "?" when the packet doesn't say."""
    if p["hops"] == 0:
        return "direct"
    return f"{p['relayNode']:02x}" if p["relayNode"] is not None else "?"


def walk_label(w: dict) -> str:
    if not w["start"]:
        return w["walk"]
    a, b = (datetime.fromisoformat(w[k]).astimezone() for k in ("start", "end"))
    return _(
        "{date} {start}–{end} · {n} Pakete",
        date=a.strftime("%d.%m.%Y"),
        start=a.strftime("%H:%M"),
        end=b.strftime("%H:%M"),
        n=w["packets"],
    )


class HeardLayer(Layer):
    id = "heard"
    name = N_("Mesh-Empfang (passiv)")
    group = N_("Abdeckung")
    description = N_(
        "Was das mitgeführte Gerät unterwegs empfangen hat, aus dem CSV-Export der "
        "Meshtastic-App und der GPX-Spur (Aufgaben → Rundgänge importieren)."
    )

    def settings(self, ctx: Context) -> list[Setting]:
        walks = list_walks(ctx)
        options = [[w["walk"], walk_label(w)] for w in walks]
        relays = {}
        for w in walks:
            counts = self._relay_counts(ctx, w)
            relays[w["walk"]] = [["", _("alle Pakete")]] + [
                [k, _("direkt ({n})", n=n) if k == "direct" else f"0x{k} ({n})"]
                for k, n in counts.most_common()
                if k != "?"
            ]
        return [
            Setting(
                "walk", _("Rundgang"), "select", options[0][0] if options else "", options=options
            ),
            Setting("window", _("Fensterlänge [min]"), "number", 5, min=2, max=15, step=1),
            Setting(
                "color",
                _("Punkte färben nach"),
                "select",
                "snr",
                options=[["snr", "SNR"], ["relay", _("letzter Hop")]],
            ),
            Setting(
                "relay",
                _("Letzter Hop"),
                "select",
                "",
                depends_on="walk",
                options_map=relays,
                help=_("Ein einzelnes Relay-Byte zeigt seine möglichen Knoten auf der Karte."),
            ),
            Setting(
                "max_km",
                _("Reichweitengrenze Relais [km]"),
                "number",
                MAX_RELAY_KM,
                min=1,
                max=50,
                step=1,
                help=_(
                    "Knoten mit passendem Endbyte, die weiter vom Empfangsort entfernt sind, "
                    "gelten nicht als letzter Hop. Annahme, kein Messwert."
                ),
            ),
        ]

    @staticmethod
    def _relay_counts(ctx: Context, w: dict) -> Counter:
        meta, records = load_heard(heard_dir(ctx) / f"{w['walk']}.jsonl")
        recv = _receiver(meta)
        return Counter(relay_key({**r, "hops": hops(r)}) for r in records if r["from"] != recv)

    def data(self, ctx: Context, values: dict) -> dict:
        walk = values["walk"] or ""
        path = heard_dir(ctx) / f"{walk}.jsonl"
        if not walk or Path(walk).name != walk or not path.is_file():  # a plain file name only
            return collection(
                [],
                note=_(
                    "Noch kein Rundgang: unter Aufgaben → Rundgänge importieren GPX und CSV "
                    "hochladen."
                ),
            )
        meta, records = load_heard(path)
        gpx = ctx.data_dir / "tracks" / meta.get("gpxName", f"{walk}.gpx")
        if not gpx.is_file():
            return collection(
                [], note=_("Die GPX-Spur {name} fehlt.", name=meta.get("gpxName", ""))
            )
        track = load_gpx(gpx)
        recv = _receiver(meta)
        nodes = meta.get("nodes", {})
        max_km = float(values["max_km"] or MAX_RELAY_KM)
        points = heard_points(records, track, recv)
        for p in points:
            p["key"] = relay_key(p)
            if p["key"] == "direct":
                p["cands"] = [{"id": node_id(p["from"]), "name": _nm(nodes, p["from"])}]
                p["grade"] = "unique"
            else:
                p["cands"] = relay_candidates(
                    nodes, p["relayNode"], (p["lat"], p["lon"]), p["from"], p["hops"], max_km
                )
                p["grade"] = relay_grade(p["cands"], p["relayNode"])

        window_s = float(values["window"] or 5) * 60
        windows = heard_windows(records, recv, track[0]["time"], track[-1]["time"], window_s)
        features = self._track(track, windows)
        order = [k for k, _n in Counter(p["key"] for p in points).most_common()]
        colors = {
            k: RELAY_COLORS[i] if i < len(RELAY_COLORS) else GREY for i, k in enumerate(order)
        }
        names = {k: self._byte_name(k, [p for p in points if p["key"] == k]) for k in order}
        sel = values["relay"] or ""
        shown = [p for p in points if not sel or p["key"] == sel]
        by_relay = values["color"] == "relay"
        for group in stacks(shown):
            main_key = Counter(p["key"] for p in group).most_common(1)[0][0]
            color = colors[main_key] if by_relay else None
            if len(group) == 1:
                features.append(self._point(group[0], nodes, color))
            else:
                features.append(self._stack(group, color))
        if sel and sel != "direct":
            features += self._candidates(shown)

        if by_relay:
            legend = {
                "title": _("Letzter Hop"),
                "items": [[colors[k], names[k]] for k in order],
            }
        else:
            legend = {
                "title": _("SNR am letzten Hop"),
                "items": [[c, label] for _limit, c, label in SNR_BANDS],
            }
        legend["items"] += [
            [TRACK_STYLE[k]["color"], _("Spur: {state}", state=_(TRACK_LABEL[k]))]
            for k in ("rx", "quiet", "none")
        ]
        note = _(
            "SNR gehört zum letzten Hop. Lücken beweisen keine fehlende Abdeckung: Es gibt "
            "keine eigenen Testpakete, nur Verkehr, der zufällig vorbeikam."
        )
        if sel and sel != "direct":
            note += " " + _(
                "Positionen der Kandidaten können absichtlich ungenau sein (bis einige km)."
            )
        if recv is None:
            note += " " + _(
                "Kein Empfänger festgelegt: unter Aufgaben → Rundgänge importieren wählen."
            )
        return collection(
            features,
            legend,
            note=note,
            stats=self._stats(points, windows, names, recv, nodes, values, max_km),
        )

    @staticmethod
    def _track(track: list[dict], windows: list) -> list[dict]:
        """The track in segments by window state, cut at the window edges (interpolated, so a
        sparse GPX still shows every window); no line across gaps in the recording."""
        times = [p["time"] for p in track]
        features = []
        for a, b, state in windows:
            inner = track[bisect.bisect_right(times, a) : bisect.bisect_left(times, b)]
            ends = [position_at(track, a), position_at(track, b)]
            coords = [ends[0]] + [(p["lat"], p["lon"]) for p in inner] + [ends[1]]
            run = [(lon, lat) for lat, lon in (c for c in coords if c is not None)]
            if len(run) >= 2:
                features.append(line(run, _title=_(TRACK_LABEL[state]), _style=TRACK_STYLE[state]))
        return features

    @staticmethod
    def _byte_name(key: str, pts: list[dict]) -> str:
        """Legend label of a relay byte: the node's short name when all its packets point to the
        same candidate (unique or likely), else the byte with a question mark."""
        if key == "direct":
            return _("direkt empfangen")
        if key == "?":
            return _("unbekannt")
        best = {_best(p) for p in pts}
        if len(best) == 1 and None not in best:
            return f"0x{key} · {best.pop()}"
        return f"0x{key} ?"

    @staticmethod
    def _point(p: dict, nodes: dict, relay_color: str | None) -> dict:
        clock = p["time"].astimezone().strftime("%H:%M:%S")
        sender = node_id(p["from"])
        name = _nm(nodes, p["from"])
        if p["key"] == "direct":
            hop = _("direkt")
        elif p["key"] == "?":
            hop = _("unbekannt")
        else:
            hop = f"0x{p['key']} · {_(GRADE_LABEL[p['grade']])}"
        fields = {
            _("Zeit"): clock,
            _("Absender"): f"{name} {sender}".strip(),
            _("Typ"): p["portnum"],
            "SNR": f"{p['rxSnr']} dB" if p["rxSnr"] is not None else "–",
            _("Hops"): p["hops"] if p["hops"] is not None else _("unbekannt"),
            _("Letzter Hop"): hop,
        }
        if p["key"] not in ("direct", "?"):
            fields[_("Kandidaten")] = "; ".join(_cand_text(c) for c in p["cands"]) or _("keine")
        color = relay_color or snr_color(p["rxSnr"])
        return feature(
            p["lon"],
            p["lat"],
            _title=_("Paket {time}", time=clock),
            _fields=fields,
            _style=hex_style(color),
            _z=1.5,
            _endpoint={
                "name": _("Empfangsort {time}", time=clock),
                "height_m": [1.0, 1.6],
                "clutter_m": 12.0,
                "device": "t1000e",
            },
        )

    @staticmethod
    def _stack(group: list[dict], relay_color: str | None) -> dict:
        """Packets received at (nearly) the same spot, e.g. while standing: one marker with the
        count, coloured by the best SNR (or the most frequent last hop)."""
        first, last = group[0]["time"].astimezone(), group[-1]["time"].astimezone()
        snrs = [p["rxSnr"] for p in group if p["rxSnr"] is not None]
        hops_ = Counter(
            _("direkt") if p["key"] == "direct" else "?" if p["key"] == "?" else f"0x{p['key']}"
            for p in group
        )
        fields = {
            _("Zeitraum"): f"{first:%H:%M:%S} – {last:%H:%M:%S}",
            _("Absender"): len({p["from"] for p in group}),
            "SNR": f"{max(snrs)} … {min(snrs)} dB" if snrs else "–",
            _("Letzter Hop"): ", ".join(f"{k} ×{n}" for k, n in hops_.most_common()),
        }
        color = relay_color or snr_color(max(snrs) if snrs else None)
        lat = sum(p["lat"] for p in group) / len(group)
        lon = sum(p["lon"] for p in group) / len(group)
        return feature(
            lon,
            lat,
            _title=_("{n} Pakete am selben Ort", n=len(group)),
            _fields=fields,
            _style=hex_style(color, size=24, text=str(len(group))),
            _z=1.5,
            _endpoint={
                "name": _("Empfangsort {time}", time=f"{first:%H:%M}"),
                "height_m": [1.0, 1.6],
                "clutter_m": 12.0,
                "device": "t1000e",
            },
        )

    @staticmethod
    def _candidates(points: list[dict]) -> list[dict]:
        """For one relay byte: its plausible candidate nodes with a position and lines from each
        packet to them; the others are listed in the packet's popup only (a node 100 km away
        would only pull the map apart)."""
        out, seen = [], {}
        for p in points:
            plausible = [c for c in p["cands"] if c["plausible"] and c["pos"]]
            for i, c in enumerate(plausible):
                out.append(
                    line(
                        [(p["lon"], p["lat"]), (c["pos"][1], c["pos"][0])],
                        _style={
                            "color": CANDIDATE_COLOR,
                            "weight": 1.5 if i == 0 else 1,
                            "opacity": 0.8 if i == 0 else 0.5,
                            "dash": None if i == 0 else "3 5",
                        },
                    )
                )
            for c in plausible:
                seen[c["id"]] = c
        for c in seen.values():
            out.append(
                feature(
                    c["pos"][1],
                    c["pos"][0],
                    _title=c["name"] or c["id"],
                    _fields={"ID": c["id"], _("Einordnung"): _("plausibel")},
                    _label=c["name"] or c["id"][-4:],
                    _style={
                        "color": "#333",
                        "fillColor": CANDIDATE_COLOR,
                        "radius": 7,
                        "weight": 2,
                    },
                    _ref={
                        "type": "heard-candidate",
                        "id": c["id"],
                        "lat": c["pos"][0],
                        "lon": c["pos"][1],
                    },
                    _endpoint={
                        "name": c["name"] or c["id"],
                        "height_m": [3.0, 10.0],
                        "clutter_m": 12.0,
                        "device": "p1pro",
                    },
                )
            )
        return out

    @staticmethod
    def _stats(points, windows, names, recv, nodes, values, max_km) -> dict:
        snrs = [p["rxSnr"] for p in points if p["rxSnr"] is not None]
        bytes_ = [k for k in names if k not in ("direct", "?")]
        clear = sum(1 for k in bytes_ if not names[k].endswith("?"))
        rx = sum(1 for _a, _b, s in windows if s == "rx")
        text = _(
            "{n} Pakete von {s} Absendern, {d} direkt, {u} mit unbekannten Hops. "
            "Relay-Bytes: {b}, davon {c} einem Knoten zuzuordnen. "
            "SNR {best} … {worst} dB. Empfang in {p} % der Fenster.",
            n=len(points),
            s=len({p["from"] for p in points}),
            d=sum(1 for p in points if p["hops"] == 0),
            u=sum(1 for p in points if p["hops"] is None),
            b=len(bytes_),
            c=clear,
            best=f"{max(snrs):.1f}" if snrs else "–",
            worst=f"{min(snrs):.1f}" if snrs else "–",
            p=round(100 * rx / len(windows)) if windows else 0,
        )
        recv_text = f"{_nm(nodes, recv)} {node_id(recv)}".strip() if recv is not None else "–"
        basis = _(
            "Empfänger {node} · Fenster {w} min · Reichweitengrenze {km} km",
            node=recv_text,
            w=f"{float(values['window'] or 5):g}",
            km=f"{max_km:g}",
        )
        return {"text": text, "basis": basis}


def _receiver(meta: dict) -> int | None:
    r = meta.get("receiver")
    return int(r[1:], 16) if r else None


def _nm(nodes: dict, num: int | None) -> str:
    return (nodes.get(node_id(num)) or {}).get("name", "") if num is not None else ""


def stacks(points: list[dict], radius_m: float = STACK_M) -> list[list[dict]]:
    """Packets grouped by place: each joins the first group whose first packet lies within
    radius_m (GPS jitter while standing), in time order."""
    groups: list[list[dict]] = []
    for p in points:
        for g in groups:
            if distance_m((g[0]["lat"], g[0]["lon"]), (p["lat"], p["lon"])) <= radius_m:
                g.append(p)
                break
        else:
            groups.append([p])
    return groups


def _best(p: dict) -> str | None:
    """The node a packet's relay byte points to (unique or likely), else None."""
    if p["grade"] not in ("unique", "likely"):
        return None
    c = next(c for c in p["cands"] if c["plausible"])
    return c["name"] or c["id"]


def _cand_text(c: dict) -> str:
    name = f"{c['name']} {c['id']}".strip()
    if c["km"] is None:
        return _("{name} (ohne Position)", name=name)
    km = f"{c['km']:.1f}"
    if not c["plausible"]:
        return _("{name} ({km} km, zu weit)", name=name, km=km)
    return _("{name} ({km} km)", name=name, km=km)
