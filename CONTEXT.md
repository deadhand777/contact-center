# Domain Context

## Response contract

The response contract is the supervisor agent's exact outcome for one customer
turn:

```json
{"answer": "...", "escalate": false, "reason": null}
```

A normal answer has `escalate=false` and `reason=null`. An escalation has
`escalate=true` and exactly one agent routing reason:

- `Kundenwunsch`
- `Sensibles Thema Kreditablehnung`
- `Systemfehler Kontodienst`
- `Kunde nicht identifiziert`
- `Keine gesicherte Antwort möglich`

Malformed supervisor output fails toward a human with
`Keine gesicherte Antwort möglich`; unvalidated model text is never shown to the
customer.

The bridge adapter uses `Systemfehler` only for infrastructure or invalid
runtime-response failures. This bridge reason is not an agent routing reason.
