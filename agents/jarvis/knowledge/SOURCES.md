# Jarvis Knowledge Sources

| Source class | Use | Boundary |
|---|---|---|
| Verified behavior and tests | Highest-confidence product behavior | Bound to exact conditions and revision |
| Repository source | Current implementation evidence | Bound to an exact commit or identified working tree |
| Chugel Mission Records | Canonical mission state and evidence | Mission 002 established bounded reads; Mission 003A extends the same `mission_query.py` seam with a frozen allow-listed learning projection |
| Human-approved product documents | Product direction | Direction is not runtime fact or execution authorization |
| External documentation and research | Supplemental evidence | Cite source and retrieval time; label uncertainty |

Knowledge modules do not import Chugel. `orchestrator/*` does not import or
consume Jarvis knowledge.
