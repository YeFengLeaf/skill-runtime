---
name: data-cleaning
description: Inspect tabular data, clean types and missing values, then report checks.
triggers: [data, csv, parquet, cleaning, missing values, schema, 数据清理, 缺失值, 清洗]
tags: [data, quality]
compatibility: [cli, mcp]
risk: normal
---

Inspect columns and sample rows before writing transformations. Keep the raw
input unchanged, make assumptions explicit, and report row counts before and
after each filtering or deduplication step.
