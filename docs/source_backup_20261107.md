# Source backup 2026-11-07

This branch preserves application source, tests and fixtures, deployment templates, research scripts, future Python/Rust code, design documents, frontend assets and the macOS launcher. It includes the P0 ECS sector/MA5 migration.

Virtual environments, actual environment files, credentials, personal trading records, runtime database/cache/logs, generated research evidence, tool indexes and macOS code signatures are excluded. Notebook code is preserved with generated outputs removed. One hardcoded data-provider credential is replaced with the TUSHARE_TOKEN environment variable. Scripts found under ignored output folders are explicitly retained. SOURCE_BACKUP_MANIFEST.json records the selected source and saved content hashes. .gitignore is adapted for the source archive.

Restore the Python environment with the dependencies in pyproject.toml. Configure real credentials locally from .env.example and deployment templates. The legacy test_trade_decision_ui.cjs additionally needs its three local research/evidence JSON files; those generated market-data archives are deliberately not distributed in this source-only backup.

The source checkout and running local web backend were preserved. Only migrated local launch paths and disposable P0 temporary files may be cleaned after remote verification. No trading or runtime state is deleted by this source backup.
