# Minimal architecture

```text
NX Python Journal ──> NX adapter ───────┐
                                        ├─> ActivityEvent v1 ─> SQLite/WAL
PowerMill .mac/API ─> PowerMill adapter ┘                         │
                                                                 v
                                                       sequence miner
                                                                 │
                                                                 v
                                                    reviewed recipe/scaffold
```

## Why this boundary

NX and PowerMill expose different automation languages, but the learning problem is the same: session, ordered action, parameters, source and optional timing. The versioned event contract lets both adapters evolve independently and lets the miner operate without either application installed.

## Performance choices

- NX Journals are parsed with Python's AST and never executed.
- Imports use SQLite `executemany` inside a single transaction.
- WAL mode allows readers while another process imports data.
- Mining counts each candidate once per session for support, while retaining total occurrences.
- The MVP has no network or model call in the hot path.

The contiguous sequence miner is intentionally simple. Replace it behind the same interface with PrefixSpan or a Rust extension only after real event volumes justify the complexity.

## Safety boundary

The output is a draft recipe and a non-mutating preview Journal. A later implementation phase must resolve stable NX objects, map calls against the target release's local stubs, and pass CAM verification. Machine-ready NC output remains under existing shop postprocessor and approval controls.

