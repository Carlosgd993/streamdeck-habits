# Changelog

Todos los cambios notables de este proyecto se documentan en este fichero.

El formato sigue [Keep a Changelog](https://keepachangelog.com/es-ES/1.0.0/).

## [Unreleased]

### Added

- `pyproject.toml` con configuracion de `ruff` (lint + formato) y `mypy`.
- `README.md` con instalacion, configuracion, uso y despliegue.
- Este `CHANGELOG.md`.
- Type hints (estilo PEP 604) y docstrings estilo Google en las APIs publicas
  de todos los modulos.

### Changed

- Orden de imports normalizado (stdlib, terceros, local) en los scripts de
  `scripts/`.
