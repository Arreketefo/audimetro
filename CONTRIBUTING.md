# Contributing

## Development

Clone `radar-core` and `audimetro` into the same parent directory, then run:

```bash
cd audimetro
make install
make check
```

`make vendor-core` refreshes the ignored shared-core snapshot. Do not commit
the generated `vendor/` directory.

## Pull request checklist

1. Keep API fixtures synthetic and remove account, channel and video identifiers.
2. Add tests for parsing, token refresh and partial-provider failures.
3. Run `make check` without live credentials.
4. Document every new environment variable in `.env.example`.
5. Keep platform-specific behavior in Audímetro and generic infrastructure in `radar-core`.
6. Update `CHANGELOG.md` for user-visible changes.

Security vulnerabilities must follow [`SECURITY.md`](SECURITY.md), not a public
issue.
