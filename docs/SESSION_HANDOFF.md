# Session handoff — AI Trading System

> **Última revisión del repositorio:** 2026-11-11 Europe/Madrid
> **Rama base:** `main`
> **Último commit integrado en `main`:** `54518975048f22e2d672ec4f01afd0d1b0a9103f` — PR #103.
> **Rama `demo`:** `34b589be83b4e35125182b511d249faf565314d7` — sincronizada mediante PR #101; contiene las fases de ejecución Demo y el ledger de posiciones.
> **Modo de ejecución:** paper trading; Binance Spot Demo aislado; live trading no implementado ni habilitado.

## Estado actual — 11 de noviembre de 2026

- PR #77 fusionada: monitor paper de stop-loss/take-profit para acciones/ETF USD de Twelve Data; CI exacto: 524 tests y Docker hardening correctos.
- PR #78 fusionada: contratos normalizados de órdenes y fills.
- PR #79 fusionada: ledger SQLite append-only de órdenes/fills, idempotencia y aislamiento por cuenta, entorno y mercado.
- PR #80 fusionada: historial de fills con completitud explícita y reconciliación read-only; no envía, cancela ni reintenta órdenes.
- PR #96 fusionada: lectura de posiciones con scope `market_type`, soporte de conectores position-only y `coverage_status=unknown` para el contrato legacy.
- PR #99 fusionada: ledger append-only de snapshots externos de posiciones, decimales exactos, hash, orden temporal y cobertura complete/partial. Una captura parcial no marca ausencias como cierres.
- PR #100 fusionada: trazabilidad experimental, diagnósticos Chief y salidas por estrategia solo ante señal booleana `False` explícita; Risk Manager sigue obligatorio.
- PR #101 sincronizó `demo` con `main`. PR #74 se cerró como implementación sustituida; su requisito de snapshot coordinado balances+posiciones sigue en roadmap y no se descarta.
- PR #103 fusionada: preflight Binance Demo de solo lectura, tres GET autenticados y validación explícita de identidad Demo, permisos y catálogo; CI exacto: 568 tests passed, 1 warning, Docker hardening passed.
- No se ha ejecutado el smoke test contra una cuenta Binance Demo real en esta sesión. No se han usado credenciales, fondos reales ni operaciones de exchange.

## Siguiente acción inmediata

Ejecutar el preflight en el servidor después de que el propietario configure allí credenciales **Binance Spot Demo** y un owner token. No enviar claves ni tokens por chat ni GitHub. Solo si el preflight pasa, seguir el runbook con una orden limit pequeña no marketable, comprobar idempotencia, persistencia tras restart, conciliación, cancelación y ledger de fills/comisiones. No afirmar que el smoke test real está completado hasta disponer de evidencia real de cada paso. Los partial fills dependen del mercado y pueden quedar como validación no observada si no ocurren.

Después del smoke test: implementar el primer adaptador real read-only de posiciones para un mercado concreto, únicamente si el proveedor ofrece cobertura verificable, timestamp de origen, identidad de cuenta/entorno/mercado y permisos mínimos. Mantener separado el snapshot ledger de posiciones del paper portfolio. Diseñar el snapshot coordinado balances+posiciones sin duplicar persistencia.

## Seguridad y límites operativos

- No introducir secretos en el repositorio, issues, logs, capturas ni conversaciones. Las claves deben permanecer en el servidor, con permisos mínimos.
- Binance Spot Demo debe usar únicamente credenciales Demo y `demo-api.binance.com`; nunca cambiar el host a producción.
- El conector Demo permite exclusivamente limit orders en el entorno virtual y no expone transferencias, retiradas ni firma.
- Los adaptadores de cuentas production existentes son read-only por implementación, pero el software no verifica independientemente los scopes reales de las API keys.
- No hay live trading habilitado ni debe activarse como parte de esta fase.
- No se ha levantado ni reiniciado una demo local en esta sesión.

## Cómo retomar

1. Leer `docs/ROADMAP.md`, `docs/BINANCE_DEMO_SMOKE_TEST_RUNBOOK.md` y `docs/ACCOUNT_PROVIDER_CONTRACTS.md`.
2. Confirmar los heads de `main` y `demo` y el estado de CI antes de editar.
3. No cerrar PRs por limpieza: evaluar dependencias, requisitos únicos y solapamientos antes de cualquier decisión.
4. Ejecutar cambios en ramas acotadas, pruebas, revisión independiente y CI exacto antes de squash merge.
5. Mantener el sistema paper/demo-only hasta que exista una fase live separada, aprobada y revisada.
