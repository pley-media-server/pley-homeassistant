# Instructions for coding agents

Pley for Home Assistant is a custom integration, installed through HACS,
that shows the devices playing from a [Pley](https://github.com/pley-media-server/pley)
media server as media players in Home Assistant, and controls them. It
talks to the server over Pley's HTTP API, as the server's
[docs/API.md](https://github.com/pley-media-server/pley/blob/master/docs/API.md)
describes it.

## Nothing comes from the Pley repository

This repository is under the Apache License 2.0. The Pley server and its
apps (`pley-media-server/pley`) are under the AGPL-3.0, and its Kodi
add-on under the GPL-2.0. Copying from either would bind this code to
their licenses, so:

- Never copy code, comments, tests or documentation from the Pley
  repository or its Kodi add-on into this one, not even a few lines, and
  not reworded line by line. Don't open the Kodi add-on's code at all.
- Write from the API's facts (routes, fields, values, status codes), in
  your own words. Reading the server's source to confirm a fact is fine;
  carrying its code or wording across is not.
- The one exception is Pley's logo, rendered as the images in
  `custom_components/pley/brand/`. Only the images, never a script that
  renders them.

This matters most when you also work in the Pley repository: keep the two
apart.

## Layout

- `custom_components/pley/`: the integration, domain `pley`.
  - `pleyclient/`: the Pley API client, typed (`py.typed`). It imports
    nothing from Home Assistant or the integration, so it can become a
    library on PyPI, as Home Assistant core requires.
  - `strings.json`: the integration's text. `translations/en.json` is
    generated from it by `script/translations.py`; never edit it by hand.
  - `quality_scale.yaml`: Home Assistant's quality scale, aiming at
    Platinum. Update a rule's status when a change does or undoes it, and
    give each exempt rule a comment saying why.
  - `brand/`: the icon, at 256 and 512 pixels.
- `tests/`: pytest, with `pytest-homeassistant-custom-component`; the
  server's answers are mocked with `AiohttpClientMocker`.
- `script/translations.py`, `.github/workflows/`, `hacs.json`.

## Check your work

Use the virtualenv in `.venv` (Python 3.14; create it with
`uv venv --python 3.14 .venv` and
`uv pip install --python .venv/bin/python -r requirements_test.txt`).
Every one of these passes before a commit:

```sh
.venv/bin/ruff check .
.venv/bin/ruff format --check .
.venv/bin/mypy
.venv/bin/python script/translations.py && git diff --exit-code custom_components/pley/translations
.venv/bin/pytest --cov=custom_components.pley --cov-report=term-missing --cov-fail-under=100
```

Coverage is 100%. Reach it with tests, not by excluding code: no
`pragma: no cover` and no weakened checks. Don't skip or loosen a test to
make it pass; if a test is wrong, say why in the commit. CI also runs the
tests against the oldest Home Assistant in `hacs.json`, so use nothing
newer than that version has.

## Writing

- Code reads like the code around it. Comments explain why, in plain
  sentences. Typing is strict (mypy `strict`), docstrings follow Google's
  style, and ruff uses Home Assistant core's rules.
- Docs and strings are plain and in the present tense, written for the
  person using Home Assistant. The product is written Pley; ids, the
  domain and packages stay lowercase `pley`.
- No license headers in source files.
- Notes for the maintainer (open questions, to-dos, test logs) go in a
  `*.local.md` file, which git ignores, never in a tracked file.
- A change people notice gets a line under `## Unreleased` in
  `CHANGELOG.md`, as a user sees it.

## Commits

- One logical change per commit, with its tests, docs and changelog.
- The subject is a plain sentence saying what changed as a user sees it,
  with no area prefix: "Show each Pley device as a media player". The body,
  if any, is short: why, and what a reviewer should know.
- No `Co-Authored-By` trailer, no "Generated with" line.
- Never commit `.venv`, caches, `.coverage`, `.hypothesis`, or anything
  `.gitignore` excludes.
