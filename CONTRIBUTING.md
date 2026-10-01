# Contributing

Issues, ideas and pull requests are welcome. This is a hobby project, so answers may take a
few days.

## Development setup

```powershell
python -m pip install -e ".[sim,dev]"
pytest                 # unit tests, no device needed (~1 min)
pytest -m hardware     # tests that talk to a connected device
pytest -m data         # regression test against a local reference scene (data/sim/reference/)
ruff check .           # lint (line length 100)
ruff format .          # format
python scripts/mapapp.py --simulate --open   # the map app with a simulated radio
```

Python changes in the map app need a server restart; the page's files are served fresh
(Ctrl+F5). After adding or upgrading dependencies in `pyproject.toml`, refresh the lock file:

```powershell
python -m pip freeze --exclude-editable | Out-File -Encoding utf8 requirements.lock
```

## Where things are

```
src/meshplay/          shared code: device connection, settings, packets, walks, probes
src/meshplay/sim/      coverage simulation: ITU models, laser-scan scenes, link prediction
src/meshplay/mapapp/   map app server: layers, background tasks, messaging, coordination mode
webmap/                map app page (HTML, CSS, JavaScript modules, offline libraries)
scripts/               command-line tools (docs/scripts.md)
config/                example configuration
docs/                  documentation
experiments/           throwaway explorations, one dated folder each
tests/                 pytest tests
data/                  your local data, never committed
```

New map layers and background tasks plug in without touching the page: see
[docs/mapapp.md](docs/mapapp.md) (*Adding a layer*, *Adding a task kind*). Open work is collected in
[docs/backlog.md](docs/backlog.md).

## Ground rules

- **No personal data in the repository.** It is public: no real coordinates, addresses, node
  IDs or names. Use placeholders (`!abcd1234`, sites `HOME`/`ROOF`, public places as example
  coordinates). Everything personal belongs in `data/` and `.env`.
- **Don't transmit while testing.** Sending reaches other people's devices. Test with the fake
  interfaces in `tests/` and the map app's `--simulate` mode.
- **Translations.** The interface is written in German and translated to English and French
  (`webmap/i18n/`); `tests/test_i18n.py` checks that every text is marked and translated.
- **Code, comments, docs and commit messages are English.** Short docstrings that say why,
  type hints, no dead code.
- `src/meshplay/sim/p1812.py` is a verbatim port of the ITU reference implementation under the
  ITU licence: don't reformat it, and note every change with date and nature in its header.

The full set of conventions (written for coding agents, useful for people too) is in
[CLAUDE.md](CLAUDE.md).
