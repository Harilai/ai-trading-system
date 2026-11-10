# Session handoff — AI Trading System

> **Purpose:** permitir que una nueva conversación retome el desarrollo sin depender del historial del chat.
>
> **Última revisión del repositorio:** 2026-11-10  
> **Rama base revisada:** `main`  
> **Último commit observado en `main`:** `23cfcb597c78060d37d190ebff9820c5c24f3716` — merge de PR #41.  
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
- Twelve Data Basic opcional para datos e investigación read-only de acciones/ETF estadounidenses.
- Scout, Charts, Strategy Research, Chief, Risk Manager y Sniper integrados.
- Backtesting cronológico 70/30, benchmark buy-and-hold, walk-forward y sensibilidad de costes.
- Worker persistente con cola durable, leases, reintentos y ciclos periódicos.
- Órdenes paper con comisión, slippage, stop-loss/take-profit e idempotencia por `client_order_id`.
- Dashboard, health/readiness, controles autenticados y backups SQLite.
- No hay ejecución real, adaptadores de órdenes live, wallets, firma blockchain, broker de acciones, derivados ni DeFi. No habilitarlos como parte de esta fase.

## 4. Fase completada: cartera paper multidivisa para acciones y ETF (PR #41)

**Integrada en main** mediante PR #41 (merge commit `23cfcb597c78060d37d190ebff9820c5c24f3716`). La cartera mantiene efectivo y posiciones en EUR/USD, FX explícito y auditado, valoración consolidada en EUR y órdenes manuales paper de cash equities/ETF estadounidenses.

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

- Rama: `phase/multicurrency-paper-portfolio`.
- PR: https://github.com/Harilai/ai-trading-system/pull/41
- Head integrado: `bcacf13c3d804715a9039a17dec89e2c19ca3db0`; merge commit: `23cfcb597c78060d37d190ebff9820c5c24f3716`.
- Se ha añadido ledger EUR/USD, migración aditiva desde `account.cash`, conversión paper idempotente y auditada, FX Twelve Data con caché/cuota y validación estricta de timestamp/tasa.
- La posición conserva coste medio nativo y coste medio EUR; cada fill USD registra tasa, proveedor y timestamp FX. P/L de ventas USD se calcula en EUR. Los costes de conversión reducen el P/L realizado EUR.
- Se ha añadido ruta manual, autenticada y paper-only para cash equities/ETF USD descubiertos por Twelve Data. Requiere saldo USD preconvertido, mercado abierto, cotización fresca, spread máximo cuando bid/ask están disponibles, slippage paper explícito, clave idempotente y aprobación del Risk Manager.
- El perfil equity tiene límites separados configurables: 10% máximo por orden, 20% de concentración por instrumento y 5% de distancia máxima de stop, además de comisión, slippage y spread propios.
- El Chief y el `decision_cycle` continúan limitados a crypto spot EUR. Las acciones/ETF no entran en selección autónoma.
- El dashboard muestra saldos por divisa y moneda nativa de precios. El formulario de conversión/órdenes no está integrado en la UI; solo existe API autenticada.
- CI del head integrado: 270 pruebas Python correctas; Docker Compose, build de imagen y verificación de ejecución no-root correctos.
- **Limitación importante:** el stop-loss de una posición equity se almacena como nivel protector, pero el worker todavía no lo monitoriza/ejecuta automáticamente. La venta paper debe ser manual hasta que se implemente la supervisión de equities; no presentar ese stop como una orden activa.
- Se han añadido pruebas unitarias e integración; el CI del último head está en cola/ejecución. No fusionar hasta confirmar suite completa, Docker hardening y revisión independiente en el SHA exacto.

### Plan de implementación

1. Añadir contrato y adaptador FX Twelve Data, incluyendo caché/cuotas, validación de tasa positiva/finita, timestamp y antigüedad máxima.
2. Evolucionar SQLite con saldos por divisa, registro de conversiones y metadatos FX en trades/posiciones; conservar migración segura de bases existentes y compatibilidad de API donde proceda.
3. Añadir conversión paper explícita EUR/USD con coste configurable, control de fondos, idempotencia y auditoría.
4. Añadir valoración multidivisa: efectivo, posiciones, equity, exposición y P/L en EUR, con desglose original por moneda y estado de valoración completa/incompleta.
5. Extender contratos de instrumentos y Risk Manager para permitir únicamente cash equities/ETFs USD de Twelve Data en paper, manteniendo bloqueados otros activos, mercados y proveedores sin soporte.
6. Mantener la investigación como read-only hasta que todos los gates de cartera, FX, sesión bursátil, liquidez, costes, stop/riesgo y pruebas estén satisfechos. No activar todavía órdenes autónomas de equities en esta primera entrega.
7. Añadir pruebas de migración, FX stale/futuro/malformado, falta de cuota, conversiones duplicadas, saldo insuficiente, movimientos entre divisas, valoración parcial, cambios de FX, comisiones y regresión crypto EUR.
8. Tras completar esta fase, abordar el ciclo de vida y conciliación de órdenes como siguiente prioridad.

### Criterios de aceptación

- Las bases existentes migran sin alterar el saldo EUR ni las posiciones crypto actuales.
- EUR y USD se contabilizan por separado; ninguna compra gasta una divisa distinta de la cotizada sin conversión paper explícita.
- Toda conversión conserva evidencia reproducible de tipo, proveedor, timestamp, costes e importes.
- Equity y exposición se reportan en EUR sin ocultar los saldos/valores originales en USD.
- Una cotización FX ausente, stale, futura, no finita o no positiva bloquea conversiones y produce valoración incompleta; nunca se inventa un tipo.
- El Risk Manager sigue siendo obligatorio y no permite shorting, margin, derivados, broker live ni wallets.
- La suite completa y CI deben pasar en el SHA exacto de la PR antes del merge.

## 5. Objetivo de producto y siguientes fases

El objetivo final no es un bot exclusivamente crypto. Debe evolucionar a una plataforma multiactivo y multi-venue preparada para capital real: exchanges como Binance, brokers y wallets, y el catálogo de instrumentos que cada proveedor/cuenta permita. El alcance objetivo incluye crypto CEX/DEX, acciones, ETF, FX, futuros, opciones, bonos, materias primas y otros mercados integrables. La elegibilidad depende de proveedor, país, cuenta, API y liquidez; descubrir un símbolo no habilita su trading.

La arquitectura de referencia está en `docs/MARKET_EXPANSION_ARCHITECTURE.md`.

Siguiente secuencia:
1. Completar el ciclo de vida, conciliación y recuperación de órdenes/fills, incluida la recuperación de tareas y leases expirados.
2. Construir abstracciones multi-venue para cuentas y ejecución: balances, permisos, order entry, cancelación, fills y capabilities; inicialmente sin live.
3. Generalizar ledger e instrumentos multiactivo: precisión/ticks, multiplicadores, vencimientos, margen/colateral, settlement, corporate actions, funding y FX.
4. Implementar copias de seguridad externas fuera del volumen/servidor Docker, con verificación de integridad y restauración probada.
5. Ampliar el dashboard con gráficos, investigación, salud de conectores, órdenes/fills y retiradas.
6. Preparar despliegue persistente 24/7 en VPS: TLS/reverse proxy, secretos, monitorización, alertas, readiness, recuperación y backups off-host.
7. Evaluar live limitado por venue/clase solo tras sandbox, conciliación, pruebas adversas, revisión de seguridad y habilitación explícita del propietario.

## 6. Límites que no se deben saltar

- Mantener paper trading como único modo operativo hasta que cada venue/clase supere su proceso de madurez y el propietario habilite explícitamente live.
- Live trading no puede activarse desde agentes, LLM, dashboard ni configuración accidental.
- No solicitar ni guardar seeds, claves privadas ni credenciales en Git, logs o frontend.
- No dar permisos de retirada a claves de trading.
- Solo crypto spot EUR está habilitado para decisiones autónomas. La PR #41 añadió órdenes manuales paper para cash equities/ETF USD, siempre owner-authenticated y con conversión explícita; no habilita equities en Chief.
- El stop-loss/take-profit de equities queda guardado como metadato y no está monitorizado por el worker todavía; las salidas deben ser manuales.
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
