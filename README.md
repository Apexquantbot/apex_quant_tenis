# Apex Quant Tenis

Versión de Apex Quant para tenis: circuitos **ATP y WTA** (individuales).
Misma estructura, estilo y funcionamiento que Apex Quant fútbol y baloncesto. Se ve dentro de **Apex Quant Deportes** (pestaña 🎾).

- Web / app: https://apexquantbot.github.io/apex_quant_tenis/
- Canal de Telegram: https://t.me/apex_quant_tenis

## Cómo funciona

| Pieza | Qué hace |
|---|---|
| `bot_quant.py` | El bot. Se ejecuta cada 15 minutos con GitHub Actions. |
| `datos.json` | Lo que muestra la web (partidos, pronósticos, alertas). Lo genera el bot. |
| `elo.json` | La fuerza (Elo) de cada jugador/a, general y por superficie. Lo genera y actualiza el bot. |
| `historico_picks.json` / `historico_mensual.json` | Libro de todas las picks liquidadas (no se reinicia los lunes). |

**Fuentes de datos (gratis):**
- Calendario, directo y resultados: ESPN.
- Histórico para arrancar el modelo: ESPN, último año (los históricos de Jeff Sackmann se retiraron de GitHub en noviembre de 2026; si vuelven, el bot los usa solo).
- Cuotas: The Odds API (cubre los torneos grandes: Grand Slams, Masters 1000, WTA 1000, Finals…).

**Modelo:**
1. Elo de cada jugador/a, mezcla de Elo general y Elo de la superficie del torneo.
2. Esa probabilidad se convierte en % de puntos ganados al saque y se simulan 10.000 partidos juego a juego
   (con desempates y al mejor de 3 o de 5) → ganador, total de juegos y hándicap de juegos.

**Pronósticos:**
- Con cuotas: se comparan las cuotas reales de las casas con el consenso del mercado y con el modelo.
  Solo se envía alerta si hay ventaja (+EV ≥ 3 %) y el modelo la respalda.
- Sin cuotas (torneo no cubierto o créditos agotados): pronóstico del modelo para favoritos claros (≥ 70 %)
  con una **cuota mínima**: solo tiene valor si tu casa paga esa cuota o más.
- Si un jugador se retira o hay walkover, el bot da la apuesta por anulada (devuelta). Cada casa tiene sus normas.

## Secretos del repositorio (Settings → Secrets and variables → Actions)

| Nombre | Obligatorio | Valor |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | Sí | Token del bot de Telegram (puede ser el mismo bot del baloncesto) |
| `TELEGRAM_CHAT_ID` | Sí | `@apex_quant_tenis` (o el nombre del canal que crees) |
| `THE_ODDS_API_KEY` | Recomendado | Clave de The Odds API (mejor una propia, distinta de la de fútbol y baloncesto) |

## Lanzamiento cada 15 minutos (cron-job.org)

- URL: `https://api.github.com/repos/Apexquantbot/apex_quant_tenis/actions/workflows/bot.yml/dispatches`
- Método POST · cabeceras `Authorization: Bearer <token>` y `Accept: application/vnd.github+json` · cuerpo `{"ref":"main"}`

🔞 +18 · Juega con responsabilidad · Información estadística, no es asesoramiento.
