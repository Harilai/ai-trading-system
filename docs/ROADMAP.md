# Engineering roadmap

Actualizado: 2026-11-09. Este documento distingue lo implementado y validado de lo que sigue pendiente. Una tarea no se considera terminada solo por existir código o pruebas: debe superar CI y revisión funcional.

## Estado integrado

| Área | Estado |
|---|---|
| Backend FastAPI y paper portfolio SQLite | Implementado |
| Datos públicos crypto spot vía CCXT | Implementado; solo datos públicos |
| Contratos normalizados de proveedores e instrumentos | Implementado |
| Registro de proveedores y capability matrix | Implementado |
| Descubrimiento multi-provider para adaptadores configurados | Implementado en datos; actualmente un proveedor por defecto |
| Router de ejecución normalizado | Contrato inicial; ningún adaptador live |
| Perfiles de riesgo por clase/tipo de mercado | Contratos implementados; solo crypto spot habilitado |
| Indicadores técnicos | Implementado |
| Contabilidad de comisiones en coste base y P/L realizado | Implementado |
| Validación de datos obsoletos y órdenes | Implementado |
| Worker persistente, cola, leases y reintentos | Implementado |
| Programación periódica de decision_cycle sin solapamientos | Implementada; 5 min por defecto, solo con worker habilitado |
| Recuperación de leases expirados en ejecución | Implementado |
| API de control autenticada | Implementada; endurecimiento de despliegue pendiente |
| Scout: descubrimiento de spot EUR por volumen/spread/frescura | Implementado |
| Charts Agent | Implementado |
| Strategy Research: EMA, RSI, Bollinger, split 70/30 y costes | Implementado como investigación, no aprobación de trading |
| Chief: selección de oportunidad y propuesta buy/sell/hold | Implementado con Risk Manager obligatorio |
| Chief Agent y coordinación de decisiones | Implementado inicialmente; ciclo aún proposal-only |
| Risk Manager conectado obligatoriamente a órdenes | Pendiente |
| Sniper y generación de propuestas | Pendiente |
| Ejecución paper desde `decision_cycle` y monitorización de stop/take | Implementada; ciclo bajo demanda, no programado |
| Acciones/ETF y FX | Pendiente; requiere broker/data adapters y validación |
| DEX, wallets, blockchain y DeFi | Pendiente; no hay integración ni firma |
| Futuros, opciones, margin y derivados | Deshabilitados; perfiles y límites específicos pendientes |
| News/Sentiment con fuentes auditables | Pendiente |
| Gestor 50/50 de beneficios y retiradas manuales | Pendiente |
| Dashboard | Pendiente |
| Infraestructura gratuita/Oracle Always Free y despliegue 24/7 | Pendiente |
| Live trading | No implementado; deshabilitado |

## Prioridad 1 — calidad de datos y estrategia

- [ ] Ampliar el universo de mercado sin exceder límites de rate ni filtros de liquidez.
- [ ] Añadir pruebas de descubrimiento con tickers ausentes, mercados inactivos, spreads extremos y fallos de proveedor.
- [ ] Verificar cálculos de indicadores con series de referencia conocidas.
- [ ] Mejorar backtesting con métricas de retorno, drawdown, profit factor, exposición y sensibilidad a costes.
- [ ] Añadir validación walk-forward, ventanas múltiples y prevención de sobreajuste.
- [ ] Persistir versiones de estrategias, resultados y motivos de promoción/rechazo.
- [ ] Clasificar régimen de mercado y evitar seleccionar una estrategia por una sola ventana.

## Prioridad 2 — orquestación segura

- [x] Implementar Chief con contratos tipados y decisiones auditables (sin ejecución automática).
- [x] Integrar Scout, Charts y Strategy Research en una tarea de decisión bajo demanda; programación recurrente pendiente.
- [ ] Implementar Sniper para generar propuestas con entrada, stop-loss, take-profit y tamaño.
- [x] Conectar Risk Manager como puerta obligatoria para propuestas del Chief; la conexión con el motor paper queda pendiente.
- [x] Añadir idempotencia de órdenes paper mediante `client_order_id` vinculado a la tarea.
- [ ] Completar estados legales de órdenes, conciliación y recuperación frente a fallos en cada frontera de persistencia.
- [ ] Implementar paper execution y monitorización de posiciones con pruebas de extremo a extremo.
- [ ] Mantener el sistema en hold/cash si no existe una oportunidad validada.

## Prioridad 3 — contabilidad y control del capital

- [ ] Añadir valoración a mercado y separar coste base, valor actual, P/L realizado y no realizado.
- [ ] Implementar reserva del 50% de beneficios netos realizados y reinversión del 50%.
- [ ] Implementar solicitudes manuales de retirada de cualquier importe disponible, con confirmación del propietario.
- [ ] Calcular mínimos y costes de retirada según proveedor; nunca conceder permisos de retirada a claves de trading.
- [ ] Recalcular riesgo y capital viable tras una retirada.

## Prioridad 4 — cobertura y producto

- [ ] Implementar y validar un adaptador de broker de acciones/ETF, horarios, permisos, datos, mínimos y comisiones.
- [ ] Generalizar el portfolio a claves `provider_id + instrument_id` y reconciliación multi-proveedor.
- [ ] Implementar adaptadores FX, índices y commodities solo tras validar APIs y elegibilidad.
- [ ] Diseñar wallet read-only y blockchain monitoring antes de añadir firma aislada; no usar seeds en agentes.
- [ ] Implementar DEX swaps con simulación, gas, slippage, MEV y reconciliación antes de habilitar ejecución.
- [ ] Mantener margin, futures, options y apalancamiento deshabilitados hasta completar sus perfiles y pruebas independientes.
- [ ] Añadir FX con tipos y timestamps verificables antes de mezclar divisas.
- [ ] Añadir News/Sentiment solo con atribución de fuente, frescura y calidad.
- [ ] Crear dashboard responsive con cartera, posiciones, investigación, decisiones, auditoría y controles.
- [ ] Añadir PostgreSQL/migraciones cuando la carga lo justifique.

## Prioridad 5 — despliegue y operación

- [ ] Evaluar Oracle Cloud Always Free y alternativas en el momento del despliegue; no asumir disponibilidad permanente.
- [ ] Mantener operación sin APIs de inferencia de pago; LLM local opcional y lógica determinista como fallback.
- [ ] Añadir health/readiness, monitorización, backups y restore probado.
- [ ] Endurecer Docker: usuario no root, secretos, TLS/reverse proxy y acceso restringido.
- [ ] Medir disponibilidad real antes de afirmar que funciona 24/7.

## Seguridad no negociable

- Paper trading hasta completar pruebas y revisión separada de live execution.
- Un provider solo se considera disponible si está registrado, sus capacidades están declaradas y se han probado; no inferir ejecución desde datos públicos.
- Las capacidades no implementadas (balances, órdenes, cancelación, partial fills, reconciliación, withdrawals, signing) deben permanecer false.
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
