# Telemetry Export

## Metadata

- **Entry Path:** features/telemetry-export
- **Status:** current
- **Evidence Level:** source
- **Last Verified:** 2026-08-20
- **Category:** feature

## Summary

OpenTelemetry span export to DuckDB with batched writes and streaming support.

## Source Paths

- otel/
- report/

## Export Formats

| Format | Renderer | Streaming |
|--------|----------|-----------|
| JSON | JSONRenderer | Yes |
| Markdown | MarkdownRenderer | Yes |
| HTML | HTMLRenderer | Yes |
| PDF | PDFRenderer | No |
| SVG | SVGRenderer | No |

## Pipeline

1. OTel spans collected in memory
2. Background thread flushes every 1s
3. Batch insert to DuckDB (max 500 spans)
4. Report engine renders to multiple formats

## Related Entries

- modules/otel
- modules/report-engine
