# Long operational incident investigation

Inspect every file under `logs/` and `config/` in batches. Reconstruct the
incident timeline from early, middle, and late evidence; several entries are
noise and one is explicitly unverified. Then write `INCIDENT_REPORT.md` with
the exact lines `ROOT_CAUSE=stale-route`, `MITIGATION=reload-after-route-fix`,
and `RECOVERY=healthcheck-green`, plus a concise timeline and all inspected
IDs (`001` through `012`). The final diagnosis must combine evidence rather
than trust a single late log line. Do not call an UNVERIFIED entry a fact.
