# Runtime Metrics

- phase: `phase6_nlu_ga`
- events(windowed): `5000` / raw `19489`
- route_rate: `0.6638`
- fallback_rate: `0.3362`
- attack_route_rate: `0.7821` (499/638)
- suspicious_attack_route_rate: `0.0000` (0/499)
- unknown_route_reason_rate: `0.0000` (0/3319)
- router_unmatched_fallback_rate: `0.1701` (286/1681)
- latency_p95_ms: `8.02`

## Per Agent
- p0_p1_regression: events=90 route=85 fallback=5 route_rate=0.9444
- phase6_runtest: events=347 route=302 fallback=45 route_rate=0.8703
- runtime_runtest: events=4518 route=2908 fallback=1610 route_rate=0.6436
- smoke: events=45 route=24 fallback=21 route_rate=0.5333
