# Engineering roadmap

> Continuity between ChatGPT sessions: read [`docs/SESSION_HANDOFF.md`](SESSION_HANDOFF.md) for project state and [`docs/AGENT_WORKFLOW.md`](AGENT_WORKFLOW.md) for the mandatory multi-agent development method.

Actualizado: 2026-11-11 (PR #132 integrada; main/demo sincronizadas tras documentación). Este documento distingue lo implementado y validado de lo que sigue pendiente. Una tarea no se considera terminada solo por existir código o pruebas: debe superar CI y revisión funcional.

## Completado — promoción de snapshots y reconciliación de ramas (#99–#106)

- PR #99 porta a `main` el ledger append-only de posiciones externas y sus endpoints autenticados. Requiere cobertura `complete/partial`, scope `market_type` en cuentas multi-market y proveedor read-only; los conectores actuales aún no habilitan captura.
- PR #100 porta la trazabilidad experimental y el endurecimiento de salidas de Chief desde `demo`, conservando el monitor equity paper de #77.
- PR #101 sincroniza `demo` con `main` usando un merge con ambos padres tras comprobar que no existían archivos exclusivos de `demo` ausentes en `main`.
- PR #103 incorpora el preflight Binance Demo read-only. PR #104 actualiza el handoff operativo. PR #106 sincronizó `main` y `demo`; PR #110 incorporó el diagnóstico seguro de claves de balance Binance Demo en `demo`. Tras PR #110, ambos árboles tienen 109 archivos y los 109 blobs son idénticos. CI de PR #110 pasó en el SHA exacto `cbbd1510476a1ddb165aa70b3d027c0816ce9598` (Python tests y Docker build/runtime hardening). PR #105 se cerró como intento amplio sustituido por conflictos.
- PR #74 se cerró como implementación sustituida. El snapshot coordinado balances+posiciones sigue pendiente como requisito, no se descarta.

## Implementado — snapshot coordinado balances + posiciones (PR #113 fusionada)

El diseño está documentado en [`COORDINATED_ACCOUNT_SNAPSHOT_DESIGN.md`](COORDINATED_ACCOUNT_SNAPSHOT_DESIGN.md). PR #113 implementa el `capture_id` común, ledger de referencias sin duplicación, cobertura explícita, identidad proveedor/cuenta/entorno/mercado, timestamp provenance y skew máximo, idempotencia, rollback atómico de ambos ledgers y endpoints autenticados de captura/historial/detalle. El ledger de balances queda aislado por entorno mediante migración compatible (filas legacy -> `production`). CI exacta del head `800f8fd63d87fb8f1c6e2ed5c5bc12adb926b0b0`: 581 tests passed, 1 warning; Docker Compose, build y non-root hardening correctos. La captura real sigue bloqueada hasta que un proveedor declare balances+posiciones y cobertura completa/parcial verificable; los conectores Spot actuales no cumplen. No inventar posiciones a partir de balances o fills.



## Adaptador Alpaca Paper read-only — integrado en main y demo (PR #118 / #120)

- PR #118 fusionada en `main` (merge SHA `d42b9fada02d60d7073bbc76171bd69c31a9e8a3`) y PR #120 sincronizada a `demo` (merge SHA `23fa997a878da7fe7ba7735b2baa80f035f7a8d7`). Proveedor aislado de posiciones abiertas con host Paper fijo, GET a `/v2/account` y `/v2/positions`, verificación obligatoria del account ID esperado y normalización decimal estricta.
- El conector solo declara `positions=true`, `market_types=[cash]`, `balances=false` y todas las capacidades de escritura en false. No se integra con cartera paper, Chief, Risk Manager, snapshot coordinado ni ejecución.
- Solo acepta `us_equity`, normalizado como `equity`; no adivina si un activo es ETF. Usa `asset_id` como identidad primaria y retrieval timestamp local. Una lista vacía es completa únicamente tras validar cuenta y endpoint.
- Activación opt-in: `ALPACA_PAPER_ENABLED=false`; requiere key, secret y `ALPACA_PAPER_EXPECTED_ACCOUNT_ID`. Sin ID esperado no se registra.
- CI del head exacto de PR #118: 609 tests Python passed; Docker build/runtime hardening passed. CI de sincronización #120 también pasó. No se ha validado contra una cuenta Alpaca real ni se han usado credenciales.
- PR #123 fusionada en `main` (merge SHA `05870aa8c3f10b75a8d1bf46dd4856df1a3575ae`) y PR #124 sincronizada a `demo` (merge SHA `6445726293db4fd408bfa046b1b4d746bbeabc3e`). ADR `docs/ALPACA_ACCOUNT_FINANCIAL_SEMANTICS_ADR.md` define el modelo tipado para cash, equity, buying power, margen, valoración, transferencias pendientes y fees. La lectura de `/v2/account` no se declara como `AccountBalance`; no se activa `balances=true` ni se extiende aún el snapshot coordinado. CI de #123 y #124: Python tests y Docker build/runtime hardening pasaron.

## Ledger financiero Alpaca Paper y snapshot coordinado — integrados (PR #127 / #128)

- PR #127 integra el ledger SQLite append-only de métricas financieras Alpaca Paper, con captura autenticada, request_id idempotente, hash de contenido, identidad de cuenta, entorno Paper y contrato tipado separado de `AccountBalance(free/used/total)`.
- PR #128 integra el snapshot coordinado cash que enlaza ledger financiero y ledger de posiciones en una transacción atómica, con referencias, cobertura, timestamps/skew, idempotencia y rollback.
- Ambos adaptadores siguen siendo read-only. No envían órdenes, no modifican PaperPortfolio, Risk Manager o Chief y no permiten transferencias ni retiradas.
- CI de PR #127 y #128 pasó en sus respectivos heads. No se ha validado todavía contra una cuenta Alpaca Paper real.
- El siguiente paso no es rehacer estos ledgers: es conectar sus evidencias a una capa de reconciliación/portfolio read-only solo después de verificar semántica, cobertura y procedencia.

## Objetivo de producto

Construir una plataforma algorítmica multiactivo y multi-venue, preparada para capital real y extensible a los instrumentos accesibles mediante los proveedores conectados. El universo objetivo no se limita a crypto spot ni a EUR: incluye crypto CEX/DEX, acciones, ETF, FX, futuros, opciones, bonos, materias primas y otros instrumentos cuando existan adaptador, elegibilidad, datos, contabilidad y perfil de riesgo adecuados.

"Operar cualquier cosa del mercado" significa poder integrar progresivamente los instrumentos que permitan los proveedores y la cuenta; no implica disponibilidad universal. Discovery, market data, paper y live execution son capacidades separadas. Ningún instrumento se habilita automáticamente por aparecer en el catálogo.

La arquitectura objetivo y los controles obligatorios están en [MARKET_EXPANSION_ARCHITECTURE.md](MARKET_EXPANSION_ARCHITECTURE.md).

## Siguiente secuencia de desarrollo acordada

1. **Completado — cartera paper multidivisa para acciones/ETF (PR #41):** efectivo EUR/USD separado, conversión FX explícita y auditable, valoración y riesgo consolidados en EUR. Las órdenes equity siguen siendo manuales y paper-only; el Chief no opera equities autónomamente.
2. **Completado — ciclo de vida y conciliación paper (PR #44):** intentos persistentes, fills idempotentes, conciliación contra el ledger, recuperación de tareas con leases y caducidad de aprobaciones stale.
3. **Completado — backups externos y restauración (PR #48):** replicación S3-compatible verificada, status API y descarga/validación de restore candidate. La configuración real del proveedor sigue pendiente en el entorno operativo.
4. **Completado en PR #50 — dashboard ampliado:** velas OHLC, salud/readiness de conectores, investigación de acciones/ETF, historial de estrategias, intentos/fills paper, backup status y gestión contable de retiradas paper. No hay transferencia real.
5. **Completado — contrato normalizado de instrumentos v2 (PR #52):** venue/native symbol, precision mode, tick/step, amount/cost bounds, contract size, settlement, expiry, strike, option right, underlying y trading status. No añade aún ejecución de nuevos mercados.
6. **Completado — catálogo persistente y venue-aware identity (PR #54):** snapshots, first/last seen, filtros, discovery history, fallos sanitizados e identidad canónica paralela al ID histórico.
7. **Completado — preparación de operación 24/7 (PR #56):** healthcheck de backups, reintentos, rotación de logs, runbook VPS y systemd unit. El VPS real aún no está desplegado.
8. **Completado — Binance Spot read-only (PR #57):** consulta autenticada de balances con permisos mínimos, sin órdenes ni retiradas; requiere configurar una API key read-only en el servidor.
9. **Completado — account snapshot ledger y conciliación read-only (PR #59):** historial inmutable por cuenta/venue, snapshots por activo, deltas exactos y separación de la cartera paper.
10. **Completado — Binance Spot Demo Trading connector (PR #61):** CCXT Demo mode, verificación estricta de `demo-api.binance.com`, balances virtuales y discovery Spot.
11. **Completado — lifecycle de órdenes Binance Spot Demo (PR #63):** limit orders, intents persistentes, clientOrderId, timeout/unknown-state reconciliation, partial fills, cancelación, auditoría append-only y fail-closed. No existe fallback a producción.
12. **Siguiente — smoke tests operativos con una cuenta Binance Demo real:** seguir `docs/BINANCE_DEMO_SMOKE_TEST_RUNBOOK.md` y verificar respuestas reales, filtros/precisión, partial fills, cancelación, timeout y reconciliación tras restart. Credenciales Demo solo en el servidor; no se simula una prueba real si el propietario no ha configurado la cuenta.
    **Preparación completada (PR #103):** preflight automatizado de solo lectura con tres GET, validación explícita de Demo/permisos/catálogo y CI verde (568 tests; Docker hardening). Esto no sustituye el smoke test real ni crea/cancela órdenes.
13. **Completado en PR #66 — ledger idempotente de fills individuales y comisiones:** trade IDs, order ID, cantidad, precio, quote cost, comisión/activo, timestamp, persistencia append-only y bloqueo fail-closed hasta cubrir el fill acumulado.
14. **Completado en PR #68 — reconciliación periódica Demo:** polling de órdenes abiertas/unknown y fills con leases persistentes, fencing, backoff, lotes acotados y recuperación tras restart; funciona con estrategia pausada y nunca reenvía órdenes.
15. **Completado — contratos normalizados de cuenta (PR #71):** registry multi-account, permisos y balances normalizados.
16. **Completado — adaptador genérico CCXT read-only (PR #72):** CEX Spot configurable por exchange ID, Kraken por defecto; consulta balances sin métodos de escritura.
17. **Completado — contrato y endpoint de posiciones normalizadas (PR #73):** snapshot tipado por proveedor/cuenta/entorno, validación decimal e identidad de instrumento y capability gate. Los adaptadores actuales siguen declarando `positions=false`; endpoint preparado para proveedores que implementen lectura de posiciones.
18. **Completado — ledger append-only de posiciones multi-venue (PR #99):** snapshots separados de la cartera paper, con identidad por proveedor/cuenta/entorno/mercado, decimales exactos, hash, orden temporal y cobertura complete/partial. Las ausencias de snapshots parciales quedan como `not_observed`; solo una captura completa puede marcar `absent`.
19. **Completado en PR #75 — demo local paper:** Compose aislado con 10.000 EUR virtuales, datos públicos Kraken, clave Twelve Data opcional solo para cotizaciones, sin credenciales de cuenta ni ruta de ejecución. CI: 460 tests Python y validación/build Docker correctos en el SHA `7abd5f527612d0c593e93d8a94852cc29d0a72d8`.
20. Supervisión paper equity y operación 24/7: monitor stop/take equity integrado en paper; VPS real, proveedor de backups externo y drills de restauración siguen pendientes.
21. Live controlado por venue/clase: solo tras smoke tests, sandbox, conciliación, pruebas adversas, revisión de seguridad y habilitación explícita del propietario. Sigue fuera del alcance de esta fase.

## Completado — trazabilidad experimental paper y salidas seguras de Chief (#100)

- Persiste aptitud experimental, motivos de validación, puntuación y señales actuales de entrada/mantenimiento dentro de la evidencia de investigación.
- Los registros antiguos se exponen como `experimental_status=legacy` y `experimental_eligible=null`; no se convierten silenciosamente en no aptos.
- Chief expone diagnósticos de candidatos, señales, exclusiones y rechazos. Las salidas por estrategia requieren `current_hold_signal is False` explícito; señal ausente o malformada equivale a mantener.
- Stops/take-profits persistidos siguen siendo salidas válidas, pero todas las ventas pasan por Risk Manager. Paper-only; no se relajan gates ni límites.

## Estado integrado

| Área | Estado |
|---|---|
| Contratos normalizados de account providers | Integrado en PR #71 (`a7a9bb8`); 433 tests, Docker hardening y endpoints genéricos autenticados |
| Adaptador CCXT read-only multi-CEX | Integrado en PR #72 (`c44bc97`); Spot balance reads only, sin operaciones de escritura |
| Contrato y endpoint de posiciones normalizadas | Integrado en PR #73 (`d9fefaf`); CI Python y Docker correctos; capability gate, sin adaptador de lectura real conectado |
| Adaptador CCXT read-only multi-CEX | Integrado en PR #72; balance Spot only, API key scope no verificado por software, sin operaciones de escritura |
| Catálogo persistente de instrumentos | Implementado y fusionado en PR #54 (`b9dbba5`); 323 tests y Docker hardening correctos |
| Preparación operativa 24/7 | Implementada en PR #56 (`c892ff3`); 330 tests y Docker hardening correctos; VPS pendiente |
| Binance Spot account connector (read-only) | Implementado en PR #57 (`8773d75`); 345 tests y Docker hardening correctos; API key no configurada |
| Snapshots y conciliación read-only de cuentas | Implementado en PR #59 (`b3cca3d`); 360 tests y Docker hardening correctos; no altera cartera paper |
| Binance Spot Demo Trading | Implementado en PR #61; balances virtuales y discovery Spot. PR #63 añade limit orders Demo con lifecycle persistente; desactivado por defecto y sin ruta de producción |
| Binance Spot Demo order lifecycle | Implementado y fusionado en PR #63 (`013f44f`); 400 tests y Docker hardening correctos; Demo-only, limit orders y cap 25 quote por defecto |
| Binance Spot Demo individual fill/fee ledger | Implementado y fusionado en PR #66 (`0f667db`); 410 tests y Docker hardening correctos; append-only, idempotente y fail-closed ante evidencia incompleta |
| Binance Spot Demo periodic reconciliation | Implementado y fusionado en PR #68 (`8943fde`); 420 tests y Docker hardening correctos; SQLite lease fencing, backoff y worker independiente del estado de estrategias |



| Contrato normalizado de instrumentos v2 | Implementado y fusionado en PR #52 (`1d6ad47`); 312 tests y Docker hardening correctos; no habilita trading de derivados |
| Dashboard de supervisión y operaciones paper | Implementado y fusionado en PR #50 (`848d1d3`); 305 tests y Docker hardening correctos |
| Backups externos S3-compatible y restore candidate verificado | Implementado y fusionado en PR #48 (`ed08cec`); off-host desactivado por defecto hasta configurar `.env.backup`; 304 tests y Docker hardening correctos |
| Ciclo de vida y conciliación de órdenes paper | Implementado y fusionado en PR #44 (`df76011`); 292 tests y Docker hardening correctos |
| Cartera paper multidivisa EUR/USD, conversiones FX y manual equity/ETF paper | Implementado y fusionado en PR #41 (`23cfcb5`); 270 tests y Docker hardening correctos |
| Backend FastAPI y paper portfolio SQLite | Implementado |
| Datos públicos crypto spot vía CCXT | Implementado; solo datos públicos |
| Datos públicos US equities/ETF vía Twelve Data Basic | Adaptador de datos/FX; órdenes manuales paper locales habilitadas para cash equity/ETF USD; sin broker ni ejecución real |
| Investigación técnica y backtesting inicial de acciones/ETF | Pipeline manual y tarea autónoma periódica de solo lectura implementados; sin propuestas ni órdenes |
| Contratos normalizados de proveedores e instrumentos | Implementado |
| Identidad de posiciones por `provider_id + instrument_id` | Implementado; migración SQLite incluida |
| Aislamiento de posiciones con mismo símbolo entre proveedores | Implementado y probado |
| Valoración de cartera por instrumento y proveedor | Implementado |
| Registro de proveedores y capability matrix | Implementado |
| Descubrimiento multi-provider para adaptadores configurados | Implementado en datos; actualmente un proveedor por defecto |
| Router de ejecución normalizado | Contrato inicial; ningún adaptador live |
| Perfiles de riesgo por clase/tipo de mercado | Crypto spot autónomo y cash equity/ETF USD manual-only paper habilitados; margin/derivados deshabilitados |
| Indicadores técnicos | Implementado |
| Contabilidad de comisiones en coste base y P/L realizado | Implementado |
| Validación de datos obsoletos y órdenes | Implementado |
| Worker persistente, cola, leases y reintentos | Implementado; readiness comprueba DB y heartbeat reciente |
| Programación periódica de decision_cycle sin solapamientos | Implementada; 5 min por defecto, solo con worker habilitado |
| Recuperación de leases expirados en ejecución | Implementado; el worker registra fallos del bucle y continúa tras errores transitorios |
| API de control y órdenes paper autenticadas | Implementadas con token; Docker no-root, rootfs read-only, capacidades eliminadas y bind local por defecto |
| Scout: descubrimiento de spot EUR por volumen/spread/frescura | Implementado |
| Charts Agent | Implementado |
| Strategy Research: EMA, RSI, Bollinger, split 70/30, OHLCV validado, benchmark buy-and-hold y gates walk-forward | Implementado como filtro de investigación; no habilita ejecución |
| Chief: selección de oportunidad y propuesta buy/sell/hold | Implementado con Risk Manager obligatorio |
| Chief Agent y coordinación de decisiones | Implementado; ciclo ejecuta paper bajo gate de riesgo |
| Risk Manager conectado obligatoriamente a órdenes paper | Implementado; crypto spot EUR autónomo y cash equities/ETF USD manual-only paper con límites propios |
| Sniper y generación de propuestas | Implementado; propuestas deterministas bajo Risk Manager obligatorio |
| Ejecución paper desde `decision_cycle` y monitorización de stop/take | Crypto spot EUR por ciclo; monitor equity paper USD cada minuto, independiente de la pausa de estrategias; sin ejecución live |
| Datos de acciones/ETF estadounidenses | Adaptador Twelve Data Basic y pipeline autónomo read-only; órdenes manuales paper USD integradas, sin broker ni ejecución real |
| Ejecución real multi-venue (CEX/broker/DEX/wallet) | No implementada; requiere adaptadores autenticados, elegibilidad, sandbox y gates de seguridad |
| DEX, wallets, blockchain y DeFi | Pendiente; sin conexión, firma ni ejecución |
| Futuros, opciones, margin y derivados | Deshabilitados; perfiles y límites específicos pendientes |
| News/Sentiment con fuentes auditables | Pendiente |
| Valoración a mercado, P/L realizado/no realizado | Implementado en cartera paper; depende de cotización fresca |
| Reserva 50/50 de beneficio neto realizado | Implementada como cálculo de reserva; política provisional configurable pendiente |
| Solicitud/aprobación/cancelación manual de retiradas paper | Implementada solo sobre efectivo EUR; desglose auditado entre beneficio reservado y capital no reservado; sin liquidación automática |
| Dashboard | MVP responsive integrado: cartera, posiciones, operaciones, estado/heartbeat, tareas y controles autenticados; gráficos avanzados y gestión de retiradas siguen pendientes |
| Infraestructura gratuita/Oracle Always Free y despliegue 24/7 | Pendiente |
| Live trading | No implementado; deshabilitado |

## Prioridad 1 — calidad de datos y estrategia

- [x] Rotar de forma persistente el muestreo estratificado del catálogo de acciones/ETF para cubrir progresivamente el universo sin exceder cuotas.
- [x] Añadir pruebas de descubrimiento con tickers ausentes/malformados/no finitos, mercados inactivos, cotizaciones stale/futuras, spreads extremos y volúmenes inválidos; los payloads defectuosos se descartan sin abortar el universo.
- [x] Rechazar OHLCV no finito, volúmenes negativos, timestamps duplicados/desordenados y velas OHLC inconsistentes antes del backtest.
- [x] Verificar SMA, EMA, RSI, MACD, Bollinger y ATR con series de referencia; rechazar valores no finitos y parámetros inválidos.
- [x] Añadir benchmark buy-and-hold OOS con la misma fracción de exposición y retorno excedente por estrategia.
- [x] Añadir escenarios de sensibilidad de comisiones y slippage a 1,5x y 2x; exigir rentabilidad neta y superar buy-and-hold en 1,5x para promocionar, mantener 2x como diagnóstico.
- [x] Añadir tres ventanas walk-forward diagnósticas y métricas por ventana.
- [x] Exigir tres ventanas walk-forward con mínimo de 40 velas evaluables y una operación cerrada por ventana, al menos dos ventanas positivas, media positiva, drawdown limitado y superar buy-and-hold neto de costes; devolver motivos de rechazo.
- [x] Persistir por instrumento las versiones/configuración de estrategia, resultados OOS, benchmark, walk-forward, sensibilidad de costes y motivos de promoción/rechazo; escritura idempotente por tarea.
- [x] Añadir diagnóstico de estructura (tendencia alcista/bajista/rango) mediante efficiency ratio y volatilidad relativa mediante percentil rolling; se persiste como contexto y no modifica gates ni autoriza operaciones.

## Prioridad 2 — orquestación segura

- [x] Implementar Chief con contratos tipados y decisiones auditables.
- [x] Programar ciclos periódicos cada 5 minutos cuando el worker está habilitado.
- [x] Integrar Scout, Charts y Strategy Research en una tarea de decisión bajo demanda; programación recurrente pendiente.
- [x] Implementar Sniper determinista separado para proponer entrada, stop-loss, take-profit y tamaño según ATR, presupuesto de riesgo neto de costes estimados de entrada/salida, asignación máxima y efectivo; Risk Manager sigue siendo el gate obligatorio.
- [x] Conectar Risk Manager como puerta obligatoria para propuestas del Chief y órdenes paper.
- [x] Añadir idempotencia de órdenes paper mediante `client_order_id` vinculado a la tarea.
- [x] Completar estados legales de órdenes paper (PR #44) y lifecycle limit-order Binance Spot Demo (PR #63), con idempotencia, reconciliación y fail-closed.
- [x] Añadir reconciliación periódica automática de órdenes Demo abiertas/unknown tras restart, con lease, rate limit y sin reenvío ciego.
- [x] Añadir monitorización paper de stop-loss/take-profit equity con cotizaciones USD y FX frescas, cierres idempotentes, recuperación tras reinicio y ejecución independiente de la pausa de estrategias (PR #77; CI exacto: 524 tests passed, 1 warning; Docker hardening passed).
- [x] Implementar paper execution y monitorización de posiciones con pruebas de extremo a extremo.
- [ ] Mantener el sistema en hold/cash si no existe una oportunidad validada.

## Prioridad 3 — contabilidad y control del capital

- [x] Añadir valoración a mercado y separar coste base, valor actual, P/L realizado y no realizado.
- [x] Calcular reserva del 50% de beneficios netos realizados y reinversión del 50% (valor por defecto provisional).
- [x] Permitir solicitudes manuales sobre cualquier efectivo disponible, con aprobación explícita, reserva de efectivo para solicitudes pendientes y desglose entre beneficio reservado y capital no reservado.
- [ ] Calcular mínimos y costes de retirada según proveedor; nunca conceder permisos de retirada a claves de trading.
- [ ] Recalcular riesgo y capital viable tras una retirada.
- [x] Persistir fills individuales de Binance Demo con trade ID, cantidad, precio, comisión y activo de comisión; reconciliar idempotentemente contra el exchange antes de usar balances Demo como evidencia contable.

## Prioridad 4 — cobertura y producto

- [x] Implementar adaptador de datos Twelve Data Basic para catálogo, cotizaciones y OHLCV de acciones/ETF estadounidenses (sin órdenes).
- [x] Implementar pipeline manual de investigación de acciones/ETF con frescura, divisa USD, bid/ask, Charts y backtesting 70/30.
- [x] Integrar investigación de acciones/ETF en el worker con tarea read-only independiente cada 6 horas, muestreo acotado, cuotas persistentes, estado de mercado, liquidez y calidad de cotización.
- [ ] Integrar acciones/ETF en decisiones del Chief y Risk Manager solo después de validar estrategia, capital, moneda y reglas de cartera específicas; sin ejecución por ahora.
- [ ] Implementar y validar un adaptador de broker de acciones/ETF, horarios, permisos, datos, mínimos y comisiones.
- [x] Generalizar el portfolio a claves `provider_id + instrument_id`, con migración de datos legacy y aislamiento de símbolos duplicados.
- [ ] Completar conciliación multi-proveedor de órdenes, fills parciales y estados de ejecución (PR #78 normaliza órdenes/fills; PR #79 añade el ledger; PR #80 explicita la completitud de historial; aún faltan adaptadores y worker de conciliación).
- [x] Añadir contrato y endpoint autenticado para posiciones normalizadas, exigiendo capacidad explícita y sin sintetizarlas desde balances.
- [x] Diseñar e implementar snapshot coordinado de cuenta que vincula balances y posiciones mediante un grupo append-only, referencias a los ledgers existentes, scope común, cobertura explícita, skew temporal, idempotencia y persistencia atómica (PR #113). Captura operativa pendiente de proveedor compatible.
- [ ] Implementar adaptadores FX, índices y commodities solo tras validar APIs y elegibilidad.
- [ ] Diseñar wallet read-only y blockchain monitoring antes de añadir firma aislada; no usar seeds en agentes.
- [ ] Implementar DEX swaps con simulación, gas, slippage, MEV y reconciliación antes de habilitar ejecución.
- [ ] Mantener margin, futures, options y apalancamiento deshabilitados hasta completar sus perfiles y pruebas independientes.
- [ ] Añadir FX con tipos y timestamps verificables antes de mezclar divisas.
- [ ] Añadir News/Sentiment solo con atribución de fuente, frescura y calidad.
- [x] Crear dashboard responsive MVP con cartera, posiciones, operaciones, heartbeat, tareas y controles autenticados; pendientes gráficos avanzados, vistas detalladas de investigación y retiradas.
- [ ] Añadir PostgreSQL/migraciones cuando la carga lo justifique.

## Prioridad 5 — despliegue y operación

- [ ] Evaluar Oracle Cloud Always Free y alternativas en el momento del despliegue; no asumir disponibilidad permanente.
- [ ] Mantener operación sin APIs de inferencia de pago; LLM local opcional y lógica determinista como fallback.
- [x] Añadir liveness y readiness con verificación SQLite y heartbeat del worker, integrado en el healthcheck Docker.
- [x] Añadir backup SQLite online con integrity_check, restauración con copia previa, servicio Docker aislado de backup diario y replicación S3-compatible cifrada con verificación SHA-256. Configuración del proveedor y restore drills en el VPS siguen pendientes.
- [x] Proteger el endpoint manual de órdenes paper con token y gate obligatorio del Risk Manager; compras con stop-loss explícito.
- [x] Ejecutar Docker como usuario no root, rootfs read-only, eliminar capacidades y enlazar el puerto a localhost por defecto.
- [ ] Configurar TLS/reverse proxy para acceso remoto; no exponer directamente el puerto API.
- [ ] Medir disponibilidad real antes de afirmar que funciona 24/7.

## Seguridad no negociable

- Paper trading hasta completar pruebas y revisión separada de live execution.
- Un provider solo se considera disponible si está registrado, sus capacidades están declaradas y se han probado; no inferir ejecución desde datos públicos.
- Las capacidades no implementadas por venue deben permanecer false. Binance Spot Demo admite únicamente limit orders; Binance de producción sigue read-only. Transfers, withdrawals, wallet signing y live execution permanecen deshabilitados.
- Live trading deshabilitado por defecto y nunca activable por agentes o LLM.
- Ningún agente puede cambiar sus propios límites de riesgo.
- No incluir claves de exchange/broker en Git, logs ni frontend.
- Las claves de trading no tendrán permisos de retirada.
- Si la calidad de datos, el estado del proveedor o la conciliación son inciertos, se bloquean nuevas entradas.
- No prometer rentabilidad ni seleccionar estrategias solo por resultados in-sample.

## Pruebas y definición de terminado

Cada fase debe incluir:
1. Tests unitarios de lógica y límites.
2. Tests de integración con proveedores simulados.
3. Tests de fallos, datos stale, rate limits, reinicios y duplicados.
4. CI verde sobre el commit exacto que se propone fusionar.
5. Revisión de diferencias y actualización de documentación.

No se fusionará código con CI fallida o con una protección financiera no probada.


## Snapshot coordinado Alpaca cash — integrado en main (PR #128)

- Rama `feat/coordinated-cash-account-snapshots`: añade un ledger coordinado separado para enlazar, en una única transacción SQLite, el snapshot financiero tipado de Alpaca Paper y el snapshot de posiciones `cash`. No reutiliza el ledger genérico Spot ni convierte `cash`, `equity` o `buying_power` en balances fungibles.
- Incluye identidad proveedor/cuenta/entorno/mercado, cobertura, timestamps y skew máximo, idempotencia por request ID, referencias a los dos ledgers, hashes de contenido, append-only y rollback completo.
- Endpoint autenticado: `POST /api/accounts/alpaca/financial-metrics/snapshots/coordinated?request_id=...`. Sigue siendo read-only y no importa datos a PaperPortfolio, Chief o Risk Manager.
- Estado: integrado mediante squash en `7ab1e0056c3f1fd749d26cdfc52eca0ac6abad6a`. CI de PR #128 (Python tests y Docker build/runtime hardening) pasó en el SHA de head `ef753ceb6d4f60d5d3aea03e0bfbdd8d39bd0484`. No se ha validado todavía contra una cuenta Alpaca Paper real.

## Política de ramas

- `main` es la única fuente de verdad.
- `demo` debe apuntar exactamente al mismo commit que `main`, no solo compartir archivos o tener un merge-base común.
- Flujo obligatorio: rama de trabajo → PR revisada/CI verde → merge a `main` → sincronización exacta de `demo` desde `main` → verificación de igualdad de SHA.
- El historial divergente previo de `demo` se conserva en `archive/demo-before-main-sync-2026-11-11`; no se incorpora a `main` salvo selección explícita de cambios necesarios.


## Descubrimiento multiactivo — endurecimiento del contrato de proveedores (integrado, PR #129)

- La registry valida cada instrumento descubierto contra la identidad del adaptador, las clases de activo y tipos de mercado declarados, y el filtro solicitado.
- Registros malformados o incompatibles se rechazan individualmente sin descartar instrumentos válidos de otros proveedores.
- Los errores de adaptadores se registran por tipo de excepción, no copiando texto arbitrario que podría contener credenciales o cabeceras.
- Integrado mediante squash en `e7bccdb62ddb5ac3464049c41a95686d3b45085f`. CI de PR #129: Python tests y Docker build/runtime hardening pasaron. No implica que todos los proveedores soporten todas las clases ni habilita ejecución.


## Descubrimiento multiactivo — filtros explícitos por proveedor y clase (integrado, PR #130)

- Registry: descubrimiento limitado a proveedores seleccionados, con error explícito para proveedor inexistente o clase no soportada cuando el proveedor se solicita expresamente.
- API: GET /api/markets y POST /api/instruments/refresh aceptan provider_id y asset_class; el refresh conserva el token de control.
- El filtro global por clase omite proveedores que no ofrecen esa clase sin marcarlos como fallidos; el filtro explícito por proveedor sí informa de incompatibilidad.
- Integrado mediante squash en `98760e46740556144478623929ed233f7e7402bd`. CI de PR #130: 645 tests Python pasaron (1 warning); Docker build/runtime hardening pasó. CI post-merge de `main` también pasó. Descubrimiento únicamente; no habilita negociación.


## Elegibilidad de instrumentos — evaluación conservadora (integrado, PR #131)

- Nuevo evaluador read-only: separa disponibilidad de datos de capacidad real de ejecución.
- Estados: supported, unsupported, not_configured y unknown; faltan metadatos críticos se informan explícitamente.
- Para considerar una integración potencialmente operable se exigen capacidades declaradas de órdenes, idempotencia, fills parciales, cancelación, conciliación, balances y posiciones.
- Se comprueban estado activo, base/quote, precisión, mínimos, fees, zona horaria de sesión y vencimiento/strike/tipo/underlying/contrato cuando aplican.
- Endpoint: GET /api/instruments/{catalogue_id}/eligibility.
- La API no registra ni presume adaptadores de ejecución: el resultado supported solo es posible cuando se acredita el adaptador y todos los controles. El endpoint actual pasa execution_adapter_configured=false.
- Integrado mediante squash en `9c0bca72e13ecd2edb8b2ff0649a42b0458c6940`. CI exacta PR #131: 655 tests Python passed (1 warning), Docker build/runtime hardening passed. CI post-merge pendiente.
- No envía órdenes, no cambia RiskManager y no habilita PAPER/DEMO/LIVE.


## Integración — execution eligibility gate (PR #132)

- PR #132 integrada por squash en `main`, merge SHA `15e80e59dc05348581cee8acb29f7fee76df6341`.
- `ExecutionRouter` expone lookup explícito de adaptadores registrados y valida que declaren el asset class y market type del instrumento. El registro de ejecución está separado del registro de market data y permanece vacío por defecto.
- El endpoint de elegibilidad consulta exclusivamente ese registro. Discovery/market data no pueden marcar un instrumento como operable.
- La evidencia de elegibilidad incorpora el `instrument_id` canónico.
- `ExecutionRouter.submit()` exige evidencia `supported`, con provider e instrumento coincidentes y adaptador configurado. `RiskManager.evaluate()` admite un gate fail-closed cuando recibe esa evidencia.
- Tests adversos cubren ausencia de adaptador, mismatch de identidad/capacidades, status no soportado y falta de configuración explícita.
- CI sobre head exacto `a204cdd410c64364ab3a45744a4bbdee2462187b`: Python **661 passed, 1 warning**; Docker build/runtime hardening y Compose correctos.
- Limitación deliberada: el registro de ejecución de la aplicación sigue vacío; Binance Demo especializado y Alpaca read-only no se registran como adaptadores genéricos. Por tanto, ningún instrumento queda habilitado por este cambio. LIVE continúa deshabilitado.
- Siguiente: definir registro de adaptadores con scope de cuenta/entorno/venue y capabilities de ejecución independientes de market data; adaptar Binance Demo y PAPER mediante wrappers explícitos solo tras revisar sus contratos, sin fingir capacidades de balances/posiciones que no existan.

## Execution adapter registry v2 — integrado (PR #133)

- PR #133 fusionada en `main`; merge commit `ffe940be7b7d399b8ad4409b09adf9e26c8cb872`.
- El registry mantiene scope explícito provider/venue/account/environment/market_type, entornos aislados, capacidades con procedencia y fail-closed para evidencia desconocida, ausente, futura o caducada.
- Endpoint autenticado GET `/api/execution/registrations`; inventario vacío por defecto. No se registran adaptadores automáticamente.

## Scoped account adapters — PR #134 (en curso, base main)

- Rama: `feat/scoped-account-adapters`; PR #134 apunta directamente a `main`.
- Añade wrappers de snapshots para Binance Spot Demo y PaperPortfolio, con scope explícito, balances y posiciones.
- Binance: las tenencias Spot no se etiquetan falsamente como posiciones; positions permanece UNKNOWN. El account_id es un alias de configuración, no identidad remota verificada.
- PAPER: ledger interno crypto Spot; el motor sigue siendo atómico y no declara partial fills ni cancelación de exchange. Readiness continúa fail-closed.
- Los wrappers no se registran automáticamente ni conectan rutas de envío. No se envían órdenes y LIVE sigue deshabilitado.
- Revisión técnica, decisión de aprobación y merge rutinario: responsabilidad del Engineering Lead; no solicitar al propietario revisión o merge.
- CI del head previo `a7e4b035a04bb3961697f9528865438e34db1511` pasó en run `43049679926`; los cambios posteriores requieren CI verde sobre el head actualizado.
- Siguiente trabajo: completar revisión técnica de #134, preservar precisión decimal, completar tests adversos y después integrar snapshots/freshness/scope/reconciliation en preflight y RiskManager sin bloquear cierres, reducciones ni emergency stop.

## Gobernanza de PR y continuidad — vigente 2026-11-11

- El propietario delega en el Engineering Lead la revisión técnica integral, la decisión de aprobación, la publicación y el merge de PR rutinarias. No solicitar al propietario revisión, aprobación o merge.
- Cada PR debe pasar revisión interna completa del diff, tests adecuados, CI verde sobre el SHA exacto y evaluación de seguridad/regresiones antes de fusionarse.
- No autoaprobar mediante la API de reviews de GitHub ni omitir branch protection. Si GitHub exige una aprobación de otra cuenta, documentar únicamente ese bloqueo de plataforma.
- PR #133 ya está integrada en `main` (merge `ffe940be7b7d399b8ad4409b09adf9e26c8cb872`). PR #134 está abierta sobre `main`; su CI del head `a7e4b035a04bb3961697f9528865438e34db1511` pasó (run `43049679926`). La revisión técnica completa y decisión de merge corresponden al Engineering Lead.
- Última comparación observada: `main` = `ffe940be7b7d399b8ad4409b09adf9e26c8cb872`; `demo` = `4ea6f3525c7b6a2826b8025d5916714179ecbde6`. Sincronizar demo al SHA final de main y verificar identidad exacta.
