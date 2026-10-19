# Data formats

## Financial statements file

Upload with `PUT /api/v1/instruments/{EXCHANGE:SYMBOL}/financials` (JSON body). One document per
company. Monetary values use the stated `unit` and are normalised to INR crore on load.

```json
{
  "symbol": "INFY",
  "exchange": "NSE",
  "currency": "INR",
  "unit": "crore",
  "source": "Company annual report FY2026",
  "source_url": "https://example.com/annual-report.pdf",
  "consolidated": true,
  "periods": [
    {
      "period_end": "2026-03-31", "period_type": "annual", "fiscal_year": 2026, "published_at": "2026-04-17",
      "revenue": 0, "cogs": 0, "ebitda": 0, "depreciation": 0, "ebit": 0, "interest": 0, "pbt": 0, "tax": 0, "pat": 0,
      "eps": 0, "equity": 0, "total_debt": 0, "cash": 0, "total_assets": 0, "current_assets": 0,
      "current_liabilities": 0, "inventory": 0, "receivables": 0, "payables": 0, "minority_interest": 0,
      "cfo": 0, "capex": 0, "dividends_paid": 0, "shares_outstanding": 0
    }
  ]
}
```

The zeros above are placeholders showing the field names; supply the reported figures. Omit a
field or set it to `null` when it is not reported; it will be shown as missing. `unit` is one of
`crore`, `lakh`, `million`, `billion`, `absolute`. `period_type` is `annual` or `quarterly`.
`shares_outstanding` is in crore shares; `eps` and `dividend_per_share` are in INR. Growth and
three-year measures need at least two and four annual periods respectively.

## Concall transcripts

`POST /api/v1/instruments/{ref}/concalls` as multipart form: `fiscal_year`, `quarter` (1–4),
`source`, optional `source_url` and `call_date`, and either `text` or a `.pdf` / `.txt` `file`
(8 MB limit). Two consecutive quarters are needed to run an analysis.

## Instrument universe (live mode)

`backend/data/reference/nse_universe.csv`: `symbol,name,sector,industry,sector_kind,instrument_type,sector_index_symbol,isin`.
`sector_kind` is `industrial`, `financial` or `index`. Lines starting with `#` are ignored.
