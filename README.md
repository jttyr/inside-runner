# inside-runner

Ejecutor de workflows (GitHub Actions) para The Inside Market Terminal.

Este repo es **publico solo para que los minutos de Actions sean gratis**; no
contiene codigo de analisis. Cada workflow descarga el repo privado
correspondiente en cada corrida (con el secreto `RUNNER_TOKEN`), lo ejecuta y
publica el resultado en la rama `data` de ese repo, de donde lo lee el
dashboard.

| Workflow | Repo privado | Disparo |
|---|---|---|
| gex-intraday / gex-open / gex-levels | gex-terminal | cron-job.org (`run-gex-*`) |
| premarket / market-open-update | market-drivers | cron-job.org (`run-premarket`) / schedule |
| fetch-macro / fetch-sectors | macro-indicators | cron-job.org (`run-macro`, `run-sectors`) |
| vol-* | vol-terminal | solo Discord (sin disparador activo) |

Todos usan `repository_dispatch` (cron-job.org, lo fiable) y, donde aplica, un
`schedule` nativo de respaldo. `keepalive` evita que GitHub apague los
`schedule` por inactividad.

## Secretos (Settings -> Secrets -> Actions)

`RUNNER_TOKEN`, `PUBLIC_IMAGES_TOKEN` y, opcionales, los webhooks de Discord
(`DISCORD_WEBHOOK_*`, `MD_DISCORD_WEBHOOK`). Sin webhooks, todo corre en modo
"solo pagina web".

Los logs de este repo son publicos: nunca imprimir secretos ni codigo privado.
