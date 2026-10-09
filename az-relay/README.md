# az-relay

A private CONNECT relay that lets the hosted ArthurLegal MCP endpoint reach e-qanun.az
(Azerbaijan's legislation database). Standard library only.

## Why

From Fly's Amsterdam region, where the main app runs, `api.e-qanun.az` does not answer
(10 s timeouts). From Frankfurt it answers in 0.5 s and from London in 0.7 s (measured
2026-10-09). The main app stays in Amsterdam and sends only its e-qanun traffic through
this relay in Frankfurt.

## What it does and does not do

- Tunnels TLS to `api.e-qanun.az:443` and `e-qanun.az:443`, and refuses everything else.
  It is not an open proxy.
- Has no public IP and no service. Only apps of the same Fly organization reach it, on
  the private network, as `arthurlegal-az-relay.internal:8080`.
- Sees host names, never content: TLS runs end to end between the main app and
  e-qanun.az.
- Allows at most 30 tunnels a minute in total, whichever machine of the main app asks.
  `RELAY_DAKIKA_AZAMI` can lower it, not raise it.
  Each main-app machine also keeps its own gate (`eqanun-api/eqanun/_gate.py`).

## Deploy

    fly apps create arthurlegal-az-relay --org personal
    fly deploy --ha=false          # from this folder; one machine holds the one budget

The main app points its e-qanun client here with
`EQANUN_PROXY = "http://arthurlegal-az-relay.internal:8080"` in its `fly.toml`.

## Test

    python -m unittest discover -s tests
