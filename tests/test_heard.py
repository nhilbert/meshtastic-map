"""Passive walks: the app's CSV export, the stored walk pair and the layer (synthetic data)."""

import json
from datetime import datetime, timedelta, timezone, tzinfo

import pytest

from meshplay.applog import (
    MissingColumns,
    hops,
    own_candidate,
    parse_app_csv,
    relay_candidates,
    relay_grade,
    time_offset_check,
)
from meshplay.config import Settings
from meshplay.mapapp import heard_store
from meshplay.mapapp.registry import Context
from meshplay.walk import heard_points, heard_windows, parse_gpx, position_at, probe_points

CEST = timezone(timedelta(hours=2))
HEADER = (
    '"date","time","from","sender name","sender lat","sender long","rx lat","rx long",'
    '"rx elevation","rx snr","distance(m)","hop limit","hop start","relay node","payload"'
)
OWN = 0xABCD0001  # the receiving device
FAR = 0x11110014  # ends in 0x14, far away (Cologne)
NEAR = 0x22220014  # ends in 0x14, near (Bonn main station)
NOPOS = 0x33330014  # ends in 0x14, no position
SENDER = 0x44440055
MARKT = (50.7353, 7.1022)  # Bonn market square
HBF = (50.7320, 7.0970)  # Bonn main station
DOM = (50.9413, 6.9583)  # Cologne cathedral


def row(t, frm, name="", spos=None, rx=MARKT, snr="-7.5", hl="2", hs="3", relay="14",
        payload="<POSITION_APP>"):  # fmt: skip
    slat, slon = spos or ("", "")
    return (
        f'"2026-10-03","{t}","{frm}","{name}","{slat}","{slon}","{rx[0]}","{rx[1]}","60",'
        f'"{snr}","","{hl}","{hs}","{relay}","{payload}"'
    )


def export(*rows: str) -> str:
    return "\n".join([HEADER, *rows]) + "\n"


def own_rows(times):
    return [row(t, OWN, "Rover", snr="0.0", hl="3", hs="0", relay="", payload="<TELEMETRY_APP>")
            for t in times]  # fmt: skip


def gpx(points) -> str:
    pts = "".join(
        f'<trkpt lat="{lat}" lon="{lon}"><time>{t}</time></trkpt>' for t, lat, lon in points
    )
    return f'<?xml version="1.0" encoding="UTF-8"?><gpx><trk><trkseg>{pts}</trkseg></trk></gpx>'


# a walk from 12:00 to 12:30 local time (10:00–10:30 UTC) along the market square
TRACK = gpx(
    [
        ("2026-10-03T10:00:00Z", *MARKT),
        ("2026-10-03T10:15:00Z", 50.7360, 7.1030),
        ("2026-10-03T10:30:00Z", *MARKT),
    ]  # fmt: skip
)
NODES = [
    row("08:00:00", FAR, "Far", DOM),
    row("08:00:05", NEAR, "Near", HBF),
    row("08:00:10", NOPOS, "Nopos"),
]


def walk_export(extra=()):
    return export(
        *NODES,
        *own_rows(["12:01:00", "12:06:00", "12:11:00", "12:21:00", "12:26:00"]),
        row("12:02:00", SENDER, "Sender", spos=HBF),
        row("12:12:00", SENDER, "Sender", payload="Guten Morgen vom Markt"),
        row("12:22:00", SENDER, "Sender", hl="3", hs="3", relay=""),
        *extra,
    )


@pytest.fixture
def ctx(tmp_path):
    return Context(Settings(port=None, data_dir=tmp_path, log_level="INFO", home=None))


def upload(ctx, csv_text=None, gpx_text=TRACK, name="walk.gpx", receiver=None):
    files = [{"name": name, "text": gpx_text}, {"name": "export.csv", "text": csv_text}]
    return heard_store.save_walk(ctx, files, receiver, CEST)


# ---------------------------------------------------------------- parser
def test_parse_columns_by_name_and_values():
    cols = HEADER.split(",")
    reordered = ",".join(cols[::-1])
    line = row("12:02:00", SENDER, "Sender", HBF, snr="0.0", relay="a").split(",")
    text = reordered + "\n" + ",".join(line[::-1]) + "\n"
    (r,), nodes, stats = parse_app_csv(text, CEST)
    assert r["from"] == SENDER and r["rxSnr"] == 0.0  # 0.0 is a value, not "missing"
    assert r["relayNode"] == 0x0A and r["portnum"] == "POSITION_APP"
    assert r["time"] == datetime(2026, 10, 3, 10, 2, tzinfo=timezone.utc)
    assert nodes["!44440055"] == {
        "name": "Sender",
        "lastSeen": r["time"].isoformat(),
        "pos": list(HBF),
    }
    assert stats == {"rows": 1, "used": 1, "skipped": {}, "start": r["time"].isoformat(),
                     "end": r["time"].isoformat()}  # fmt: skip


def test_parse_missing_column_and_unreadable_rows():
    with pytest.raises(MissingColumns) as e:
        parse_app_csv('"date","time","from"\n"2026-10-03","12:00:00","1"\n', CEST)
    assert "rx snr" in e.value.columns
    records, _nodes, stats = parse_app_csv(export(row("x", SENDER), row("12:00:00", SENDER)), CEST)
    assert len(records) == 1 and stats["skipped"] == {"unreadable": 1}


def test_parse_empty_fields_hops_and_text_messages():
    text = export(
        row("12:00:00", SENDER, snr="", relay="", hl="", hs=""),
        row("12:00:01", SENDER, hl="0", hs="0", payload="Hallo zusammen"),
        row("12:00:02", SENDER, hl="3", hs="3"),
    )
    a, b, c = parse_app_csv(text, CEST)[0]
    assert a["rxSnr"] is None and a["relayNode"] is None and hops(a) is None
    assert hops(b) is None  # hop start 0: old firmware, unknown rather than direct
    assert b["portnum"] == "TEXT_MESSAGE_APP" and "Hallo" not in json.dumps(b, default=str)
    assert hops(c) == 0


def test_parse_zone_per_date():
    class Berlin(tzinfo):  # summer time until the last Sunday of October (enough for the test)
        def utcoffset(self, dt):
            return timedelta(hours=2 if dt.month < 10 or dt.day < 25 else 1)

        def dst(self, dt):
            return timedelta(0)

    text = HEADER + "\n" + row("12:00:00", SENDER) + "\n" + row("12:00:00", SENDER).replace(
        "2026-10-03", "2026-10-30") + "\n"  # fmt: skip
    a, b = parse_app_csv(text, Berlin())[0]
    assert a["time"].astimezone(timezone.utc).hour == 10
    assert b["time"].astimezone(timezone.utc).hour == 11


def test_own_candidate_by_majority():
    rows = own_rows(["12:00:00", "12:01:00", "12:02:00"])
    rows.append(row("12:03:00", OWN, snr="-3.0", relay="01"))  # rebroadcast heard back
    rows.append(row("12:04:00", SENDER, snr="0.0", relay=""))  # one real 0.0 of another node
    rows.append(row("12:05:00", SENDER))
    assert own_candidate(parse_app_csv(export(*rows), CEST)[0]) == OWN


def test_relay_candidates_and_grades():
    nodes = parse_app_csv(export(*NODES, row("08:01:00", SENDER, "S", HBF)), CEST)[1]
    c = relay_candidates(nodes, 0x14, MARKT, SENDER, 2, max_km=15)
    assert [x["id"] for x in c] == ["!22220014", "!11110014", "!33330014"]
    assert [x["plausible"] for x in c] == [True, False, True]
    assert relay_grade(c) == "ambiguous"  # the one without position can't be ruled out
    without_nopos = [x for x in c if x["km"] is not None]
    assert relay_grade(without_nopos) == "unique"
    assert relay_grade(relay_candidates(nodes, 0x14, MARKT, SENDER, 2, max_km=100)[:2]) == "likely"
    assert relay_grade([], None) == "unknown"
    # once relayed, the original sender is no candidate for the last hop
    assert relay_candidates(nodes, 0x55, MARKT, SENDER, 1) == []
    assert len(relay_candidates(nodes, 0x55, MARKT, SENDER, 0)) == 1


def test_time_offset_check():
    track = parse_gpx(TRACK)
    rows = [row(f"12:{m:02d}:00", SENDER, rx=MARKT if m in (0, 30) else (50.7360, 7.1030))
            for m in (0, 14, 15, 16, 30)]  # fmt: skip
    ok = parse_app_csv(export(*rows), CEST)[0]
    assert time_offset_check(ok, track)["clear"] is False
    shifted = parse_app_csv(export(*rows), timezone(timedelta(hours=1)))[0]  # read one hour off
    res = time_offset_check(shifted, track)
    assert res["best_h"] == -1 and res["clear"] is True
    no_pos = parse_app_csv(export(*[r.replace(str(MARKT[0]), "") for r in rows]), CEST)[0]
    assert time_offset_check(no_pos, track)["clear"] is False


# ---------------------------------------------------------------- walk.py
def test_position_at_gap_and_probe_points():
    t = lambda m: datetime(2026, 10, 3, 10, m, tzinfo=timezone.utc)  # noqa: E731
    track = [{"time": t(0), "lat": 50.0, "lon": 7.0}, {"time": t(1), "lat": 50.001, "lon": 7.0},
             {"time": t(30), "lat": 50.01, "lon": 7.0}]  # fmt: skip
    assert position_at(track, t(0) + timedelta(seconds=30)) is not None
    assert position_at(track, t(10)) is None  # inside a 29-min gap
    assert position_at(track, t(10), max_gap_s=None) is not None
    probe = {"time": t(10), "result": "ok", "snrTowards": 1, "snrBack": 1, "rssi": -100,
             "relays": 0, "interval": 60, "preset": "ShortSlow"}  # fmt: skip
    assert probe_points([probe], track) == []


def test_heard_windows():
    records, _n, _s = parse_app_csv(walk_export(), CEST)
    track = parse_gpx(TRACK)
    w = heard_windows(records, OWN, track[0]["time"], track[-1]["time"], 300)
    assert [s for _a, _b, s in w] == ["rx", "quiet", "rx", "none", "rx", "quiet"]
    pts = heard_points(records, track, OWN)
    assert {p["from"] for p in pts} == {SENDER} and len(pts) == 3


# ---------------------------------------------------------------- store
def test_save_walk_stores_pair_without_wording(ctx):
    res = upload(ctx, walk_export())
    assert res["walk"] == "walk" and res["inWalk"] == 3
    assert res["receiver"] == "!abcd0001" and res["receiverGuessed"] is True
    assert (ctx.data_dir / "tracks" / "walk.gpx").read_text(encoding="utf-8") == TRACK
    text = (ctx.data_dir / "heard" / "walk.jsonl").read_text(encoding="utf-8")
    assert "Guten Morgen" not in text
    meta = json.loads(text.splitlines()[0])["meta"]
    assert set(meta["nodes"]) >= {"!11110014", "!22220014", "!33330014"}  # from outside the cut
    assert len(text.splitlines()) == 1 + 3 + 5  # header, packets, own packets in the walk


@pytest.mark.parametrize(
    "files, msg",
    [
        ([{"name": "walk.gpx", "text": TRACK}], "genau eine"),
        ([{"name": "walk.gpx", "text": TRACK}, {"name": "x.txt", "text": ""}], "Nur .gpx"),
        ([{"name": "walk.gpx", "text": TRACK}, {"name": "e.csv", "text": '"date"\n'}], "fehlen"),
        ([{"name": "walk.gpx", "text": "<gpx/>"}, {"name": "e.csv", "text": export()}],
         "Zeitstempel"),
        ([{"name": "walk.gpx", "text": TRACK}, {"name": "e.csv", "text": export(*NODES)}],
         "nicht übereinander"),
    ],
)  # fmt: skip
def test_save_walk_refusals_leave_nothing(ctx, files, msg):
    with pytest.raises(ValueError, match=msg):
        heard_store.save_walk(ctx, files, tz=CEST)
    assert not list(ctx.data_dir.rglob("*.gpx")) and not list(ctx.data_dir.rglob("*.jsonl"))


def test_save_walk_name_conflict_and_reupload(ctx):
    upload(ctx, walk_export())
    res = upload(ctx, walk_export([row("12:25:00", SENDER)]), receiver="!44440055")
    assert res["receiver"] == "!44440055" and res["receiverGuessed"] is False
    other = TRACK.replace("10:30:00", "10:31:00")
    with pytest.raises(ValueError, match="anderem Inhalt"):
        upload(ctx, walk_export(), gpx_text=other)
    # a GPX uploaded earlier for a traceroute walk with the same content is fine
    (ctx.data_dir / "tracks" / "probe.gpx").write_text(TRACK, encoding="utf-8")
    assert upload(ctx, walk_export(), name="probe.gpx")["walk"] == "probe"


def test_reupload_with_crlf_track(ctx):
    crlf = TRACK.replace("><", ">\r\n<")
    upload(ctx, walk_export(), gpx_text=crlf)
    assert (ctx.data_dir / "tracks" / "walk.gpx").read_bytes() == crlf.encode()  # kept as is
    assert upload(ctx, walk_export(), gpx_text=crlf)["walk"] == "walk"
    # the same track stored earlier by the plain GPX upload (raw bytes, BOM)
    (ctx.data_dir / "tracks" / "other.gpx").write_bytes(b"\xef\xbb\xbf" + crlf.encode())
    assert upload(ctx, walk_export(), gpx_text=crlf, name="other.gpx")["walk"] == "other"


def test_list_walks_and_set_receiver(ctx):
    upload(ctx, walk_export())
    (w,) = heard_store.list_walks(ctx)
    assert w["packets"] == 3 and w["senders"][0] == ["!abcd0001", "Rover", 5]
    assert w["start"] == "2026-10-03T10:00:00+00:00"  # the track's time, not the export's
    assert w["bbox"] == [[MARKT[0], MARKT[1]], [50.736, 7.103]]
    path = ctx.data_dir / "heard" / "walk.jsonl"
    body = path.read_text(encoding="utf-8").splitlines()[1:]
    w = heard_store.set_receiver(ctx, "walk", "!44440055")
    assert w["receiver"] == "!44440055" and w["packets"] == 5 and not w["receiverGuessed"]
    assert path.read_text(encoding="utf-8").splitlines()[1:] == body  # only the header changed
    with pytest.raises(ValueError, match="Empfänger"):
        heard_store.set_receiver(ctx, "walk", "!99999999")
    with pytest.raises(ValueError, match="Rundgang"):
        heard_store.set_receiver(ctx, "../walk", "!44440055")


# ---------------------------------------------------------------- layer
def test_layer_without_data(ctx):
    from meshplay.mapapp.layers.heard import HeardLayer

    layer = HeardLayer()
    data = layer.data(ctx, layer.parse_values(ctx, {}))
    assert data["features"] == [] and "Rundgänge importieren" in data["note"]


def test_layer_points_track_and_candidates(ctx):
    from meshplay.mapapp.layers.heard import HeardLayer

    upload(ctx, walk_export())
    layer = HeardLayer()
    relay = next(s for s in layer.settings(ctx) if s.name == "relay")
    assert relay.options_map["walk"] == [
        ["", "alle Pakete"],
        ["14", "0x14 (2)"],
        ["direct", "direkt (1)"],
    ]

    data = layer.data(ctx, layer.parse_values(ctx, {"walk": "walk"}))
    points = [f for f in data["features"] if f["geometry"]["type"] == "Point"]
    assert len(points) == 3 and all("_endpoint" in f["properties"] for f in points)
    assert "Guten" not in json.dumps(data)
    styles = {
        f["properties"]["_title"] for f in data["features"] if f["geometry"]["type"] != "Point"
    }
    assert styles == {"Empfang", "Gerät aktiv, nichts gehört", "keine Daten"}
    assert "3 Pakete von 1 Absendern, 1 direkt" in data["stats"]["text"]

    data = layer.data(ctx, layer.parse_values(ctx, {"walk": "walk", "relay": "14"}))
    cands = [f for f in data["features"] if (f["properties"].get("_ref") or {}).get("type")]
    assert {f["properties"]["_ref"]["type"] for f in cands} == {"heard-candidate"}
    assert {f["properties"]["_ref"]["id"] for f in cands} == {"!22220014"}  # only plausible
    lines = [f for f in data["features"] if f["geometry"]["type"] == "LineString"
             and list(f["geometry"]["coordinates"][-1]) == [HBF[1], HBF[0]]]  # fmt: skip
    assert len(lines) == 2  # one per packet with this byte, only to the plausible candidate
    assert not any(list(f["geometry"]["coordinates"][-1]) == [DOM[1], DOM[0]]
                   for f in data["features"] if f["geometry"]["type"] == "LineString")  # fmt: skip


def test_layer_stacks_packets_at_one_spot(ctx):
    from meshplay.mapapp.layers.heard import HeardLayer

    # two more packets near the one of 12:02, all within 15 m of the start
    upload(ctx, walk_export([row("12:00:20", SENDER), row("12:00:40", SENDER, snr="-2.0")]))
    layer = HeardLayer()
    data = layer.data(ctx, layer.parse_values(ctx, {"walk": "walk"}))
    marks = [f["properties"] for f in data["features"] if f["geometry"]["type"] == "Point"]
    assert all(m["_style"]["shape"] == "hex" for m in marks)
    (stack,) = [m for m in marks if m["_style"].get("text")]
    assert stack["_style"]["text"] == "3" and stack["_title"] == "3 Pakete am selben Ort"
    assert stack["_style"]["fillColor"] == "#aeea00"  # the best SNR of the stack (-2.0 dB)


def test_stacks_only_consecutive_packets():
    from meshplay.mapapp.layers.heard import stacks

    here, there = {"lat": MARKT[0], "lon": MARKT[1]}, {"lat": HBF[0], "lon": HBF[1]}
    groups = stacks([{**here, "n": 1}, {**here, "n": 2}, {**there, "n": 3}, {**here, "n": 4}])
    assert [[p["n"] for p in g] for g in groups] == [[1, 2], [3], [4]]  # back later: new group


def test_layer_with_missing_gpx(ctx):
    from meshplay.mapapp.layers.heard import HeardLayer

    upload(ctx, walk_export())
    (ctx.data_dir / "tracks" / "walk.gpx").unlink()
    layer = HeardLayer()
    data = layer.data(ctx, layer.parse_values(ctx, {"walk": "walk"}))
    assert data["features"] == [] and "walk.gpx" in data["note"]
