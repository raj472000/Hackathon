// Runtime contracts for the responses the UI depends on most. Extra fields are allowed.
import { z } from 'zod'

const num = z.number().nullable()

export const Envelope = z.object({ data_mode: z.enum(['demo', 'live']), is_demo: z.boolean(), retrieved_at: z.string() })

export const ScreenerSchema = Envelope.extend({
  items: z.array(
    z
      .object({
        instrument_id: z.number(),
        symbol: z.string(),
        name: z.string().nullable(),
        sector: z.string().nullable(),
        values: z.record(z.string(), num),
        missing: z.record(z.string(), z.object({ status: z.string(), reason: z.string() })),
        stance: z.string().nullable().optional(),
        risk_label: z.string().nullable().optional(),
      })
      .loose(),
  ),
  total: z.number(),
  universe: z.number(),
  page: z.number(),
  page_size: z.number(),
  columns: z.array(z.object({ key: z.string(), label: z.string(), unit: z.string(), group: z.string() })),
  as_of: z.string().nullable(),
  computed_at: z.string().nullable(),
  excluded_for_missing_filter_metric: z.number(),
}).loose()

export const RotationSchema = Envelope.extend({
  snapshot_id: z.number().nullable(),
  sectors: z.record(
    z.string(),
    z
      .object({
        status: z.string(),
        quadrant: z.string().nullable().optional(),
        trail: z.array(z.object({ date: z.string(), rs_ratio: z.number(), rs_momentum: z.number() })).optional(),
        reason: z.string().nullable().optional(),
      })
      .loose(),
  ),
}).loose()

export const SourcesSchema = Envelope.extend({
  connectors: z.array(z.object({ key: z.string(), label: z.string(), state: z.string() }).loose()),
  capabilities: z.record(z.string(), z.any()),
  coverage: z.object({ instruments: z.number(), last_bar: z.string().nullable() }).loose(),
}).loose()

export type Screener = z.infer<typeof ScreenerSchema>
export type Rotation = z.infer<typeof RotationSchema>
export type Sources = z.infer<typeof SourcesSchema>
