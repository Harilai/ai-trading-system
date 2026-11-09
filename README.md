# AI Trading System

Sistema de trading algorítmico asistido por agentes de IA.

## Estado del proyecto

En desarrollo. La primera fase se centra en **paper trading**: análisis de mercado y simulación de operaciones sin enviar órdenes reales a ningún broker o exchange.

## Objetivos iniciales

- Obtener datos de mercado de criptomonedas y acciones.
- Calcular indicadores técnicos como EMA, RSI y MACD.
- Gestionar una cartera virtual con saldo inicial configurable.
- Generar señales explicables de compra, venta o espera.
- Aplicar límites de riesgo antes de aceptar operaciones simuladas.
- Exponer la funcionalidad mediante una API REST.

## Seguridad

- No se envían órdenes reales.
- No guardar claves API en el repositorio.
- La ejecución real requerirá una fase independiente, configuración explícita y controles de riesgo adicionales.

## Próximos pasos

1. Definir la arquitectura del backend.
2. Implementar proveedores de datos de mercado.
3. Implementar indicadores y generación de señales.
4. Implementar cartera y ejecución simulada.
5. Añadir pruebas automatizadas y documentación.
