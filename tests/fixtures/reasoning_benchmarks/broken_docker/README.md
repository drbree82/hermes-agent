# Broken service fixture

The service listens on port 8080. The healthcheck deliberately probes a
different port. The benchmark agent should identify and correct the mismatch,
then validate the YAML and the command without requiring Docker privileges.
