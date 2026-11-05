# Contributing

The client for Pley's API lives in `custom_components/pley/pleyclient/`,
which doesn't depend on Home Assistant, so in the future it can become a
standalone library (as Home Assistant core requires). A test keeps it that
way.

```sh
pip install -r requirements_test.txt pre-commit
pre-commit install  # runs ruff and mypy before each commit
pytest --cov=custom_components.pley
python script/translations.py  # after changing strings.json
pytest --snapshot-update  # after changing entities or diagnostics; review the diff
HYPOTHESIS_PROFILE=thorough pytest tests/test_fuzz.py  # after changing parsing
```

CI requires 100% test coverage, and also runs the tests against the oldest
Home Assistant version in `hacs.json`.

`tests/test_e2e.py` checks the integration against a real Pley server, and
is skipped unless you name one. `PLEY_BIN` is a server program, which the
tests start on a new data folder and a free port; build one from the
[Pley repository](https://github.com/pley-media-server/pley) with
`cargo build -p pley-server`:

```sh
PLEY_BIN=../pley/target/debug/pley pytest tests/test_e2e.py
```

Or `PLEY_URL` is the address of a server that's running and not set up
yet, with `PLEY_SETUP_CODE`, the setup code from its log, and
`PLEY_MEDIA_DIR`, a folder the tests put a film in, which the server reads
at the same path. CI checks against a server built from Pley's master
every night, and on demand.

`translations/en.json` is generated from `strings.json`, so change the
latter.

## License

The integration is under the Apache License 2.0. Contributions come under
the same license: whatever you submit is licensed as the rest of the
repository is, as section 5 of the license says.

The integration is written from Pley's HTTP API, as the server's
[API documentation](https://github.com/pley-media-server/pley/blob/master/docs/API.md)
describes it. Nothing is copied from the Pley repository (AGPL-3.0) or its
Kodi add-on (GPL-2.0): no code, comments, tests or documentation. The only
exception is Pley's logo, in `custom_components/pley/brand/`.
