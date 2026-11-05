# Working on Pley for Home Assistant

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
  server's answers are mocked with `AiohttpClientMocker`. `test_e2e.py`
  checks against a real Pley server instead, and is skipped unless
  `PLEY_BIN` or `PLEY_URL` names one (`CONTRIBUTING.md` says how).
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
`pragma: no cover` and no weakened checks. A failing test gets fixed, not
skipped or relaxed; when the test itself was wrong, the commit message
explains how. CI also runs the tests against the oldest Home Assistant in
`hacs.json`, so use nothing newer than that version has.

## Writing

- New code matches its neighbours in naming, structure and how much it
  comments, and a comment gives the reason for the code rather than
  retelling it. Typing is strict (mypy `strict`), docstrings follow Google's
  style, and ruff uses Home Assistant core's rules.
- Docs and strings use simple words and describe what is, for the person
  using Home Assistant. The product is written Pley; ids, the
  domain and packages stay lowercase `pley`.
- No license headers in source files.
- Open questions, to-dos and test logs meant for the maintainer belong in
  a git-ignored `*.local.md` file, not in anything tracked.
- A change people notice is noted under `## Unreleased` in
  `CHANGELOG.md`, described from their side.

## Commits

- Each commit holds a single change, together with the tests, docs and
  changelog line it needs.
- The subject is a short sentence describing the change from the user's
  side, without an area prefix: "Show each Pley device as a media player".
  Add a brief body only when a reviewer needs the reason.
- Leave out `Co-Authored-By` trailers and "Generated with" lines.
- Keep `.venv`, caches, `.coverage`, `.hypothesis` and whatever
  `.gitignore` excludes out of commits.

## Releases

Versions are semantic, from 0.1.0, and tagged `v<version>`. A commit
"Release <version>" sets the version in `manifest.json` and heads the
changelog's unreleased notes `## <version> (<date>)`. Publishing the
GitHub release for its tag, with those notes, runs `release.yml`, which
checks the manifest's version matches the tag and attaches
`custom_components/pley` as `pley.zip`, the file HACS installs. A person
publishes releases: never tag or publish one yourself.
