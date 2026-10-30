# Research cost and resource control

The figures below reproduce the cost analysis in [VeriTrace, Appendix XVII, Tables 14–15](https://arxiv.org/html/2605.26081v2#A17). They are **estimated LLM token costs**, calculated from recorded per-model usage and the paper's May 2026 Alibaba Cloud Bailian rates. They are not current provider quotations or a new benchmark of this release.

## Dollar conversion and reporting basis

The paper uses **7.2 CNY per USD**. Dollar values are converted from the reported CNY totals and rounded to two decimal places:

`USD per query = CNY per query / 7.2`

| Configuration | CNY/query | USD/query | RACE Overall | RACE Insight |
|---|---:|---:|---:|---:|
| Config-3B | 5.35 | **$0.74** | 51.96 | 55.22 |
| Config-27B | 4.56 | **$0.63** | 52.60 | 56.12 |
| Config-Plus | 9.98 | **$1.39** | 53.50 | 57.08 |
| Config-DeepSeek | 14.08 | **$1.96** | 55.77 | 59.56 |

For example, `4.56 / 7.2 = 0.6333…`, reported as **$0.63**. Costs are means across 99–100 queries in each configuration's usage records. RACE values come from paper Table 2; Config-27B's score is a three-run mean. Cost and quality are separate paper reporting aggregates, not a new paired experiment. The configurations use different backbones.

Token costs include the research and writing model calls. Search-service fees, hosting, infrastructure and evaluation-judge fees are excluded from these figures. Cached-input discounts are also excluded. Actual bills depend on provider prices, model routes, task difficulty, retries and cache reuse.

The website's budget control multiplies the rounded USD/query figure by a selected query count. For example, 100 queries at Config-27B's published mean produce an illustrative **$63** model-token budget. This arithmetic is not a quote or a spending guarantee.

## Reported usage per query

| Configuration | LLM calls | Input tokens (K) | Output tokens (K) | Search calls | Wall time (minutes) |
|---|---:|---:|---:|---:|---:|
| Config-3B | 260 | 2,261 | 1,391 | 57.05 | 45.80 |
| Config-27B | 212 | 1,592 | 1,106 | 47.31 | 57.41 |
| Config-Plus | 264 | 2,224 | 1,348 | 59.45 | 64.78 |
| Config-DeepSeek | 225 | 1,750 | 461 | 46.59 | 40.61 |

These are study means. A smaller active model does not necessarily imply a cheaper complete run: token consumption and the distribution of work across roles also matter. For a new cost analysis, apply each model's input and output rates to its own usage, rather than pricing the aggregate tokens at the Planner's rate.

## How the system controls resource use

**Role-specific models.** In Config-DeepSeek, Pro handles planning, search and writing; Flash handles graph updates, reading and filtering. The paper attributes about **$1.73 (88%)** of the per-query model cost to the Pro path and **$0.23 (12%)** to Flash. The components and percentages are rounded.

**Scoped writing context.** The writing pipeline moves from an outline to section evidence plans and then section text. Writers receive the evidence bound to their section. This describes the design; the paper does not provide an isolated dollar saving for this mechanism.

**Feedback and stopping rules.** Search strategies respond to evidence gaps and quality feedback. In paper Table 5, removing deviation feedback raises search calls to **1.22 times** the full system's count. That is a search-call comparison, not a measured 22% reduction in total cost. The workflow also has iteration, attempt and time limits; see [configuration](../VERITRACE/docs/configuration.md).

The paper discusses a conditional **$0.70–$1.20** Config-DeepSeek estimate under cached-prefix reuse. That is a projection rather than a separate measured benchmark result and is not used in the website's main cost figures or budget control.
