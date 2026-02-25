# TODO - Yahooquery Hardening Roadmap

## Goal
Migliorare stabilita', performance e manutenibilita' senza rompere l'API pubblica.

## Next implementation (1m snapshots target)
- [x] Definire configurazione simboli spot 1m: `^SPX`, `^VIX`, `^VVIX` + ETF (`SVOL`, `VXX`, `UVIX`, `UVXY`, `SPY`).
- [x] Aggiungere `^SKEW` con schedulazione separata 1 volta al giorno a mercato chiuso.
- [x] Implementare `scripts/collect_1m_snapshots.py` con loop a 60s e `Ticker` riusati per tutta la sessione.
- [x] Precalcolare (refresh giornaliero) le scadenze mensili entro 12 mesi per `^SPX` e `^VIX`.
- [x] Per opzioni usare richieste per singola scadenza (`date=`) invece di full chain ad ogni ciclo.
- [x] Salvare output in formato partizionato per data/simbolo/scadenza (`data/raw_snapshots/YYYY-MM-DD/...`).
- [x] Gestire `^VVIX` come solo spot snapshot (fallback esplicito quando option chain non disponibile).
- [x] Aggiungere metriche runtime per ciclo: latenza totale, numero chiamate, errori/retry.
- [ ] Aggiungere retry/backoff + timeout separati (connect/read) configurabili da env/config.
- [x] Test unit: filtro scadenze mensili, scheduler 60s, normalizzazione schema snapshot.
- [ ] Test integration (opt-in): smoke test reale su 1 ciclo completo con guardrail di durata.

## Priority 1 (Next sessions)
- [ ] Introdurre un helper ufficiale per riuso `Ticker/sessione` (evitare reinstanziazione in loop).
- [ ] Aggiungere benchmark script versionato (`scripts/benchmark_core.py`) con output CSV.
- [ ] Uniformare il modello errori di rete (`{"error": "...", "status_code": ...}`) e documentarlo.
- [ ] Rafforzare retry/backoff su errori transienti (429/5xx) con limiti configurabili.
- [ ] Aggiungere timeout separati (connect/read) e test dedicati.

## Priority 2
- [ ] Separare test offline/unit da test rete/integration con marker pytest (`unit`, `integration`, `premium`).
- [ ] Ridurre flaky test rete con fixture mock centrali per response HTTP/JSON.
- [ ] Aggiungere CI matrix minima: Python 3.9-3.12 + pandas 2.2/3.x.
- [ ] Validazione schema `CONFIG` piu' stretta (chiavi richieste, tipi, coerenza query/response).

## Priority 3
- [ ] Migliorare docs con best practice performance:
- riuso istanza `Ticker`
- uso `asynchronous=True`
- uso `max_workers` su workload multi-symbol
- [ ] Aggiungere esempi "snapshot rapida" (`quotes`/`price`) vs endpoint pesanti.
- [ ] Valutare cache opzionale in memoria per richieste ripetute a breve distanza.

## Regression checks (mandatory before push)
- [ ] `python -m pytest tests/test_base_core.py tests/test_regressions.py -q`
- [ ] `python -m pytest tests/ -k "not test_research.py" -q`
- [ ] `pre-commit run --files <files_toccati>`

## Performance notes (baseline branch `fix/yahooquery-hardening`)
- Init `Ticker` (session setup + crumb): ~0.75-1.2s (cold first run ~5s).
- `summary_detail` (30 simboli, async 20 workers): ~0.48s.
- `quotes` (30 simboli): ~0.12s.
- `^VIX option_chain` con istanza riusata: ~0.09s.
- `^VIX option_chain` con nuova istanza ogni chiamata: ~2.1s.

## Operational note
- Non versionare artefatti runtime in `snapshots/` (usare `.gitignore` o path esterno).
