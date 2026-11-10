# Arquitectura objetivo de expansión de mercados y capital real

## Objetivo del producto

AI Trading System debe evolucionar hacia una plataforma de investigación, gestión de cartera y ejecución algorítmica multiactivo y multi-venue, preparada para operar con capital real cuando el propietario lo habilite. El núcleo no debe asumir que crypto spot EUR es el universo definitivo.

El objetivo es descubrir e integrar los instrumentos que ofrezcan los proveedores conectados y para los que la cuenta del usuario sea legal y técnicamente elegible. "Cualquier activo" significa una arquitectura extensible, no una promesa de que todos los instrumentos estén disponibles en todos los países, cuentas, exchanges, brokers o APIs.

## Clases de mercado objetivo

Incorporación progresiva, mediante adaptadores y perfiles específicos:

- Crypto spot centralizado (CEX).
- Crypto spot descentralizado (DEX), tokens y pares on-chain.
- Acciones, ETF y otros valores cotizados.
- FX spot.
- Futuros, opciones y otros derivados listados.
- Bonos, materias primas, índices y productos estructurados cuando exista proveedor y permiso adecuados.
- Otros mercados que puedan expresarse con contratos explícitos de instrumento, datos, contabilidad y riesgo.

Descubrimiento, datos de mercado, elegibilidad, simulación paper y ejecución real son capacidades distintas. Encontrar un símbolo nunca habilita por sí solo una orden.

## Capas obligatorias

1. **Instrument Master / catálogo normalizado.** Identidad estable por proveedor, venue, instrumento y clase; símbolo nativo, moneda de cotización/liquidación, precisión, tick/lot size, mínimo de orden, multiplicador de contrato, vencimiento, strike, tipo de opción, subyacente, horario, estado y restricciones.
2. **Provider & Venue Adapters.** Interfaces separadas para discovery, market data, account/balances, order entry, amend/cancel, order status, fills, transfers y capabilities. Cada adaptador declara exactamente qué soporta; nunca se infieren capacidades.
3. **Account & Wallet Connectors.** Separar conexión de solo lectura, trading y firma/transacciones. Credenciales con mínimo privilegio y sin permisos de retirada. Las claves privadas/seed phrases no se solicitan ni se guardan en el backend central. Para wallets, preferir firma local o hardware wallet y permisos por transacción.
4. **Portfolio & Ledger.** Libro mayor por cuenta, venue, activo y moneda; cash disponible/reservado, posiciones, colateral, deuda, fees, funding, dividendos, splits, corporate actions, realised/unrealised P&L y FX con origen/timestamp. No mezclar saldos de cuentas distintas.
5. **Risk Engine.** Límites por usuario, cuenta, venue, clase, instrumento y estrategia; exposición bruta/neta, concentración, liquidez, volatilidad, gaps, correlación, FX, apalancamiento, margen, liquidación y pérdida máxima. Los agentes/LLM no pueden modificar sus propios límites ni saltarse controles deterministas.
6. **Strategy & Decision Layer.** Estrategias independientes de venue, con contratos explícitos de datos, universo, horizonte, benchmark, costes y evidencia. La decisión propone; el Risk Engine autoriza o rechaza.
7. **Execution Router.** Selección de venue según elegibilidad, liquidez, costes, latencia, disponibilidad y riesgo. Estados normalizados con idempotencia, partial fills, cancel/replace, expiraciones, rechazos, reconexión y conciliación.
8. **Observability & Audit.** Registro auditable de decisiones, datos usados, versión de estrategia/modelo, aprobación de riesgo, órdenes, fills, comisiones, conversiones, acciones del operador y cambios de configuración.

## Reglas de acceso y seguridad

- Separar permisos de lectura, trading y retirada. El sistema no necesita permisos de retirada para operar.
- Las claves de exchange nunca se exponen al navegador ni a agentes LLM; usar secretos del entorno/secret manager, rotación y scopes mínimos.
- Las wallets no deben entregar seed phrases. La firma debe ser explícita, limitada y aislada del proceso de investigación/decisión.
- Ningún agente puede activar live trading, elevar límites, habilitar apalancamiento o concederse permisos.
- La ejecución real requiere habilitación explícita del propietario, por cuenta/venue y por clase de activo, después de superar pruebas y revisión.
- Ante datos stale, saldo desconocido, permisos ambiguos, estado de orden incierto, discrepancia de ledger o fallo de conciliación: bloquear nuevas entradas y preservar posiciones existentes; nunca asumir que una orden falló o se ejecutó.
- Respetar restricciones regulatorias, geográficas, de producto y de elegibilidad de cada proveedor. No intentar eludirlas.

## Secuencia de madurez por mercado

1. Contrato e identidad del instrumento y capability discovery.
2. Market data validado y pruebas de calidad/frescura.
3. Simulación/backtesting con costes, horarios, precisión y reglas reales del instrumento.
4. Cartera paper y ledger específicos del mercado.
5. Risk profile determinista y escenarios adversos.
6. Paper execution con ciclo completo de órdenes/fills y conciliación.
7. Sandbox/testnet del proveedor, si existe.
8. Revisión de seguridad, operación, restauración y monitorización.
9. Live limitado y opt-in explícito, con límites pequeños, sin permisos de retirada y kill switch.
10. Ampliación gradual solo tras evidencia verificable.

No se salta una etapa por disponer de una API key o porque el proveedor permita enviar una orden.

## Estado actual y alcance inmediato

La ejecución continúa siendo paper-only. CCXT proporciona datos públicos de crypto spot; Twelve Data proporciona datos/FX para investigación y órdenes manuales paper de cash equities/ETF USD. El sistema ya dispone de ciclo de vida paper de órdenes y conciliación, catálogo persistente venue-aware, backups off-host, dashboard y contrato normalizado de instrumentos.

La PR #57 añade un conector autenticado Binance Spot de solo lectura para consultar balances reales, protegido por owner token. No expone órdenes, transferencias, retiradas ni firma; no sincroniza esos balances con la cartera paper y no demuestra ni verifica los permisos de la API key. Todavía no hay ejecución autenticada de órdenes CEX/broker, wallet signing, DEX execution ni live trading.

La preparación operativa VPS está documentada, pero no se ha desplegado un host real. Los contratos normalizados de cuentas y el `AccountProviderRegistry` permiten registrar proveedores por `provider_id + account_alias`, normalizar balances y declarar capacidades/permisos separados. Binance Spot read-only y Binance Spot Demo se registran como cuentas distintas; solo el conector read-only puede crear snapshots inmutables. Los contratos de posiciones/órdenes son fundacionales: todavía no existe un adaptador genérico de broker ni de posiciones. El conector Binance Spot Demo Trading ya está aislado y verifica que las rutas REST Spot usan `demo-api.binance.com`; solo expone balances virtuales y discovery, sin órdenes. La siguiente fase construirá el lifecycle durable de órdenes Demo con idempotencia, reconciliación de timeouts/restarts, partial fills y cancelación. No declarar soporte de un mercado hasta que su adaptador, datos, contabilidad, riesgo y matriz de capacidades estén implementados y probados.
