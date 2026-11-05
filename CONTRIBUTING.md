# Contributing

```sh
pip install -r requirements_test.txt pre-commit
pre-commit install  # runs ruff and mypy before each commit
pytest --cov=custom_components.pley
python script/translations.py  # after changing strings.json
```

CI requires 100% test coverage, and also runs the tests against the oldest
Home Assistant version in `hacs.json`.

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
