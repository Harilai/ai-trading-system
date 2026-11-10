# Session handoff — AI Trading System

> **Purpose:** permitir que una nueva conversación retome el desarrollo sin depender del historial del chat.
>
> **Última revisión del repositorio:** 2026-11-10  
> **Rama base revisada:** `main`  
> **Último commit observado en `main`:** `ed08cec879662facbcbc0c5c2c2eb26058441133` — merge de PR #48, copias externas verificadas y restauración segura.  
> **Estado de ejecución:** paper trading únicamente; live trading no implementado y deshabilitado.

## 1. Cómo retomar en una conversación nueva

Antes de modificar código:

1. Leer este documento (`docs/SESSION_HANDOFF.md`) y el método de trabajo obligatorio (`docs/AGENT_WORKFLOW.md`).
2. Leer `docs/ROADMAP.md` y las restricciones de seguridad de `README.md`.
3. Consultar el estado actual de `main`, los últimos commits y las PR abiertas. Este documento es una guía de continuidad, no sustituye la comprobación del repositorio vivo.
4. Comparar el HEAD actual con el commit indicado arriba. Si `main` ha avanzado, revisar qué se integró y actualizar este handoff antes de continuar.
5. Continuar por **la siguiente fase pendiente** de este documento. No repetir tareas marcadas como integradas.
6. Aplicar los roles y las puertas de calidad descritos en `docs/AGENT_WORKFLOW.md`: planificación, arquitectura, investigación cuantitativa, desarrollo, riesgo/seguridad, QA, revisión independiente y release/documentación.
7. Trabajar en una rama por cambio lógico, añadir pruebas, revisar el diff y exigir CI verde antes de fusionar.

No asumir que una conversación anterior dejó cambios sin subir. El repositorio y sus PR son la fuente de verdad.

La forma de trabajo no es opcional ni depende del chat: `docs/AGENT_WORKFLOW.md` define los roles especializados, las fases, los criterios de revisión y el protocolo de cierre.

## 2. Trabajo ya completado que motivó esta continuación

- **PR #17 — Walk-forward y benchmark como gates obligatorios:** fusionada en `main` (merge commit `482b14b0b0f0af183003c49cb8d51d7e86ff6445`). Exige evidencia en tres ventanas walk-forward, operaciones cerradas por ventana, ventanas positivas, retorno medio positivo, drawdown limitado y superar buy-and-hold neto de costes. Si falta evidencia, la estrategia no se promociona.
- **PR #18 — Validación de indicadores técnicos:** fusionada en `main` (merge commit `5f2f96ba77941c28ddda7787dba29ee4fa5a39fb`). Endurece validaciones de NaN/infinito, periodos y coherencia OHLC del ATR, y añade pruebas con series numéricas de referencia.
- Después se integraron mejoras posteriores hasta la PR #38: historial/versionado de investigación, estrés de costes, diagnóstico de régimen, Sniper, cálculo de riesgo round-trip, robustez del descubrimiento de mercados, validación de entradas finitas, backups SQLite verificados y programados, y dashboard responsive.
- La PR #27 de backups quedó cerrada sin merge porque divergió de `main`; su funcionalidad fue sustituida por las PR #36 y #37, que sí están fusionadas.

## 3. Estado funcional conocido

- Backend FastAPI y cartera paper persistida en SQLite.
- Datos públicos de crypto spot mediante CCXT; Kraken es el proveedor inicial por defecto.
- Twelve Data Basic opcional para datos/FX de acciones y ETF estadounidenses; el Chief mantiene investigación read-only y las órdenes equity solo son manuales paper.
- Scout, Charts, Strategy Research, Chief, Risk Manager y Sniper integrados.
- Backtesting cronológico 70/30, benchmark buy-and-hold, walk-forward y sensibilidad de costes.
- Worker persistente con cola durable, leases con token de fencing, heartbeat, reintentos acotados y ciclos periódicos.
- Ciclo de vida paper de órdenes con intentos risk-approved persistidos, fills atómicos, eventos append-only, reconciliación contra el ledger y bloqueo fail-closed ante discrepancias.
- Órdenes paper con comisión, slippage, stop-loss/take-profit e idempotencia por `client_order_id`.
- Dashboard, health/readiness, controles autenticados y backups SQLite.
- No hay ejecución real, adaptadores de órdenes live, wallets, firma blockchain, broker de acciones, derivados ni DeFi. No habilitarlos como parte de esta fase.

## 4. Fases completadas: cartera multidivisa y conciliación de órdenes

**Completada en PR #41.** La cartera paper mantiene efectivo y posiciones EUR/USD, permite conversiones explícitas y valora acciones/ETF estadounidenses en USD dentro de una cartera cuya divisa base continúa siendo EUR. La investigación sigue siendo read-only para el Chief: las órdenes equity son manuales y paper-only.

### Decisiones de arquitectura y límites

1. EUR seguirá siendo la divisa base de reporting y la moneda del capital inicial configurable (20 EUR por defecto).
2. El efectivo se mantendrá por divisa. El saldo USD no se presumirá ni se creará automáticamente al comprar un activo cotizado en USD.
3. La conversión EUR/USD será una operación paper explícita, auditada e idempotente, con importe origen/destino, tipo de cambio, timestamp, proveedor, coste/spread simulado y referencia de cotización.
4. El tipo FX se obtendrá de Twelve Data mediante `/exchange_rate` cuando esté configurado. Debe respetar el limitador de cuota ya existente. No se usarán tipos hardcodeados ni tasas stale/futuras/malformadas. Sin FX verificable, se bloquea la conversión; la valoración queda incompleta y Chief/API bloquean nuevas órdenes hasta recuperar una valoración fiable.
5. Cada posición conservará precio/coste base en su divisa de cotización. La cartera calculará equity, exposición y P/L agregado en EUR usando tipos FX identificados y timestamped; la valoración se marcará incompleta si falta cualquier cotización o conversión requerida.
6. La ruta de acciones/ETF permanecerá paper-only y limitada a instrumentos cash equity/ETF de EE. UU. identificados por el proveedor Twelve Data. No se habilitarán brokers ni ejecución real.
7. Risk Manager deberá validar clase de activo, tipo de mercado, divisa, efectivo disponible en la divisa de cotización, exposición y tamaño expresados de forma consistente en EUR. Los límites de crypto spot actuales no se ampliarán por defecto a equities.
8. Retiradas paper existentes seguirán limitadas a efectivo EUR hasta que exista un diseño separado de retirada multidivisa. No se convertirá USD de forma implícita para financiar retiradas.

### Resultado integrado — PR #41

- PR: https://github.com/Harilai/ai-trading-system/pull/41
- Merge commit: `23cfcb597c78060d37d190ebff9820c5c24f3716`.
- Rama de trabajo: `phase/multicurrency-paper-portfolio`.
- CI sobre el head final antes del merge: 270 pruebas correctas; validación Compose, build de imagen y ejecución non-root correctas.
- Ledger EUR/USD, migración aditiva desde `account.cash`, conversiones paper idempotentes y auditadas, FX Twelve Data con caché/cuota y validación estricta.
- Posiciones USD con coste medio nativo y EUR; fills con tasa/proveedor/timestamp FX; P/L y equity consolidados en EUR. Los costes de conversión reducen el P/L realizado.
- Órdenes manuales paper para cash equities/ETF USD con efectivo preconvertido, cotización fresca, sesión abierta, control de spread cuando hay bid/ask, slippage simulado, idempotencia y Risk Manager.
- Perfil equity separado: máximo 10% por orden, 20% de concentración por instrumento, 5% de distancia máxima de stop, comisión/slippage/spread y mínimo configurables.
- Chief/decision cycle siguen limitados a crypto spot EUR; la valoración del worker sí incorpora FX y marks USD frescos cuando hay efectivo/posiciones USD. Valoración incompleta bloquea nuevas órdenes.
- Dashboard muestra saldos por divisa y monedas nativas.
- **Limitación:** stop-loss/take-profit equity se guardan, pero todavía no los monitoriza/ejecuta el worker. Las salidas equity son manuales hasta implementar esa supervisión en la siguiente fase.
- No hay broker, ejecución real, wallets, firma, margin, derivados ni retiradas USD.

### Implementación entregada

1. [x] Añadir contrato y adaptador FX Twelve Data, incluyendo caché/cuotas, validación de tasa positiva/finita, timestamp y antigüedad máxima.
2. [x] Evolucionar SQLite con saldos por divisa, registro de conversiones y metadatos FX en trades/posiciones; conservar migración segura de bases existentes.
3. [x] Añadir conversión paper explícita EUR/USD con coste configurable, control de fondos, idempotencia y auditoría.
4. [x] Añadir valoración multidivisa: efectivo, posiciones, equity, exposición y P/L en EUR, con desglose original por moneda y estado de valoración completa/incompleta.
5. [x] Extender contratos de instrumentos y Risk Manager para permitir únicamente cash equities/ETFs USD de Twelve Data en paper, manteniendo bloqueados otros activos, mercados y proveedores sin soporte.
6. [x] Mantener la investigación read-only para el Chief; permitir solo órdenes manuales paper tras los gates de cartera, FX, sesión bursátil, costes y riesgo. No activar órdenes autónomas de equities.
7. [x] Añadir pruebas de migración, FX stale/futuro/malformado, falta de cuota, conversiones duplicadas, saldo insuficiente, movimientos entre divisas, valoración parcial, cambios de FX, comisiones y regresión crypto EUR.
8. [x] PR #44 completa el ciclo de vida y conciliación paper; la siguiente fase pendiente son backups externos y restauración.

### Criterios de aceptación

- Las bases existentes migran sin alterar el saldo EUR ni las posiciones crypto actuales.
- EUR y USD se contabilizan por separado; ninguna compra gasta una divisa distinta de la cotizada sin conversión paper explícita.
- Toda conversión conserva evidencia reproducible de tipo, proveedor, timestamp, costes e importes.
- Equity y exposición se reportan en EUR sin ocultar los saldos/valores originales en USD.
- Una cotización FX ausente, stale, futura, no finita o no positiva bloquea conversiones y produce valoración incompleta; nunca se inventa un tipo.
- El Risk Manager sigue siendo obligatorio y no permite shorting, margin, derivados, broker live ni wallets.
- La suite completa y CI deben pasar en el SHA exacto de la PR antes del merge.

### Resultado integrado — PR #44: ciclo de vida y conciliación paper

- PR: https://github.com/Harilai/ai-trading-system/pull/44
- Merge commit: `df760119580b56a345d0e7c9ecdc60b45d2365dd`.
- Head validado: `0c20359375af24be79c5ebd008c8ca9fa32e84d1`.
- CI: 292 pruebas Python correctas; Compose, build Docker y ejecución non-root correctos.
- Intentos risk-approved persistidos con request/decision inmutables; fills y cambios de ledger atómicos.
- Reconciliación de intentos frente a trades, verificación del payload completo, eventos append-only y estado `reconciliation_required`.
- Recuperación tras restart con market/FX freshness, backoff y presupuesto finito; fencing por lease token y heartbeat.
- Nuevas compras bloqueadas mientras exista un intento sin resolver; las ventas de reducción de riesgo siguen permitidas.
- API owner-authenticated `/api/agent/order-intents` para consultar intents y eventos.

### Resultado integrado — PR #48: copias externas verificadas

- PR: https://github.com/Harilai/ai-trading-system/pull/48
- Merge commit: `ed08cec879662facbcbc0c5c2c2eb26058441133`.
- Head validado: `2e5b251b666df8faccf7857597bd50bc284be22d`.
- CI: 304 pruebas Python correctas; Compose, build Docker y ejecución non-root correctos.
- Replicación opcional de las dos bases SQLite a S3-compatible, con objetos content-addressed, HTTPS, cifrado server-side y verificación SHA-256 mediante read-back.
- CLI `app.backup_remote list/download`; las descargas se validan con SHA-256 e integridad SQLite y no sobrescriben candidatos existentes.
- API owner-authenticated `/api/agent/backup-status`.
- Credenciales S3 aisladas en `.env.backup`, cargadas solo en el contenedor backup; el API no las recibe.
- Retención local conserva snapshots cuyo off-host copy no está verificado. No hay borrado remoto desde la aplicación.
- **Importante:** la función off-host está desactivada por defecto. Debe configurarse el proveedor y crear `.env.backup` a partir de `.env.backup.example`.

## 5. Objetivo de producto y siguientes fases

El objetivo final no es un bot exclusivamente crypto. Debe evolucionar a una plataforma multiactivo y multi-venue preparada para capital real: conectar exchanges como Binance, brokers y wallets, y descubrir/operar los instrumentos que cada proveedor y cuenta permita. El universo objetivo incluye crypto CEX/DEX, acciones, ETF, FX, futuros, opciones, bonos, materias primas y otros mercados integrables. La disponibilidad depende del proveedor, país, cuenta, API, liquidez y elegibilidad. Descubrir un símbolo no habilita su trading.

La arquitectura de referencia está en `docs/MARKET_EXPANSION_ARCHITECTURE.md`.

Siguiente secuencia:
1. **Completado — backups externos y restauración (PR #48):** replicación S3-compatible verificada, status API y descarga/validación de restore candidate. Falta configurar el proveedor real en el entorno operativo.
2. **Siguiente fase — dashboard ampliado:** gráficos, investigación, salud de conectores, órdenes/fills, backup status y gestión de retiradas.
3. Construir abstracción multi-venue y catálogo de instrumentos: capabilities, cuentas, permisos, precisión/ticks, multiplicadores, vencimientos, margen/colateral, settlement, corporate actions, funding y FX.
4. Preparar despliegue persistente 24/7 en VPS: TLS/reverse proxy, secretos, aislamiento, monitorización, alertas, readiness y recuperación.
5. Añadir supervisión paper de stops/take-profit equity y madurez paper/sandbox por mercado antes de decisiones autónomas o live.
6. Evaluar live limitado por venue/clase solo tras sandbox, conciliación, pruebas adversas, revisión de seguridad y habilitación explícita del propietario.

## 6. Límites que no se deben saltar

- Mantener paper trading como único modo operativo hasta que cada venue/clase supere su proceso de madurez y el propietario habilite explícitamente live.
- Live trading no puede activarse desde agentes, LLM, dashboard ni configuración accidental.
- No solicitar ni guardar seeds, claves privadas ni credenciales en Git, logs o frontend.
- No dar permisos de retirada a claves de trading.
- Solo crypto spot EUR está habilitado para decisiones autónomas. Las órdenes cash equities/ETF USD son manuales y paper-only, requieren autenticación owner y conversión explícita; no habilitan equities en Chief.
- El stop-loss/take-profit de equities queda guardado como metadato y no está monitorizado por el worker todavía; las salidas deben ser manuales hasta la fase de conciliación/supervisión.
- Datos stale, inconsistentes o conciliación incierta deben bloquear nuevas entradas.
- No prometer rentabilidad; backtests y paper trading no prueban rentabilidad futura.

## 7. Actualización obligatoria al cerrar cada sesión

Al terminar una fase, actualizar este documento en el mismo PR o en un PR documental asociado:

- Fecha y SHA de `main` revisado.
- PR/commit integrado y resumen verificable.
- Pruebas ejecutadas y resultado de CI.
- Qué queda pendiente, con el siguiente paso concreto.
- Riesgos conocidos o decisiones que una nueva conversación debe respetar.

**Regla de continuidad:** una nueva conversación debe poder empezar leyendo el repositorio y continuar el siguiente paso sin pedir al usuario que reconstruya el contexto del chat.
