# Benchmark results and provenance

These results are reported in [VeriTrace, arXiv:2605.26081v2](https://arxiv.org/html/2605.26081v2), revised 31 August 2026 and accepted to Findings of EMNLP 2026. They are not fresh scores from installing the latest commit. Provider/model versions, search conditions and evaluation configuration matter when reproducing them.

## DeepResearch Bench: reported configurations

**Strongest reproducible open-source result.** Config-DeepSeek achieves **55.77 Overall** and **59.56 Insight** on DeepResearch Bench, the strongest reproducible open-source result within the paper's Table 2 comparison. This comparison uses each system's reported configuration; the matched-backbone results are listed separately below.

RACE evaluates report quality against expert reference reports. Scores use the 0–100 scale; they are not percentages of questions answered correctly. The ordering below is computed from **Table 2**, with equal Overall scores sharing a rank. Original configurations differ, so this table does not isolate architecture from model capability.

| Rank | System / configuration | Overall | Comprehensiveness | Insight | Instruction following | Readability |
|---:|---|---:|---:|---:|---:|---:|
| 1 | **VeriTrace / Config-DeepSeek** | **55.77** | 56.28 | 59.56 | 52.24 | 51.98 |
| 2 | FS-Researcher / Claude-Sonnet-4.5 | 53.94 | 54.25 | 55.85 | 52.47 | 51.54 |
| 3 | VeriTrace / Config-Plus | 53.50 | 52.84 | 57.08 | 51.65 | 49.61 |
| 4 | VeriTrace / Config-27B | 52.60 | 51.51 | 56.12 | 50.64 | 49.87 |
| 5 | VeriTrace / Config-3B | 51.96 | 50.85 | 55.22 | 50.38 | 49.08 |
| 6 | RhinoInsight / Gemini-2.5-Pro | 50.92 | 50.51 | 51.45 | 51.72 | 50.00 |
| 7 | EnterpriseDR / Gemini-2.5-Pro | 50.62 | 49.70 | 51.24 | 50.52 | 50.61 |
| 7 | WebWeaver / Qwen3-235B-A22B-Instruct-2507 | 50.62 | 51.29 | 51.00 | 49.98 | 48.89 |
| 9 | LangChain Open Deep Research / GPT-5 | 50.60 | 50.06 | 50.76 | 51.31 | 49.72 |
| 10 | Gemini-2.5-Pro Deep Research | 49.71 | 49.51 | 49.45 | 50.12 | 50.00 |
| 11 | OpenAI Deep Research | 46.45 | 46.46 | 43.73 | 49.39 | 47.22 |
| 12 | Claude DeepResearch | 45.00 | 45.34 | 42.79 | 47.58 | 44.66 |
| 13 | Kimi Researcher | 44.64 | 44.96 | 41.97 | 47.14 | 45.59 |
| 14 | Doubao DeepResearch | 44.34 | 44.84 | 40.56 | 47.95 | 44.69 |

Config-27B RACE scores are means across three independent full runs; its FACT results come from the first run. Table 2 reports effective citations / citation accuracy of 235.66 / 73.28 for Config-DeepSeek and 134.20 / 68.46 for Config-27B.

Ranks refer to this paper's comparison set, not the continuously updated [official leaderboard](https://deepresearch-bench.github.io/). Do not mix judge versions or infer a current leaderboard position from this table.

## Controlled comparison

All systems below use the shared Qwen3.5-27B backbone. Source: **Table 3**. VeriTrace and WebWeaver report mean ± standard deviation over three independent full runs.

| System | Comprehensiveness | Insight | Instruction following | Readability | Overall |
|---|---:|---:|---:|---:|---:|
| **VeriTrace** | 51.51 ± 0.65 | **56.12 ± 0.26** | **50.64 ± 0.15** | **49.87 ± 0.50** | **52.60 ± 0.31** |
| WebWeaver | **51.83 ± 0.50** | 51.30 ± 0.33 | 49.60 ± 0.47 | 49.23 ± 0.41 | 50.77 ± 0.34 |
| Enterprise-DR | 47.82 | 50.17 | 49.30 | 48.91 | 49.16 |
| FS-Researcher | 47.83 | 47.05 | 48.74 | 49.37 | 48.03 |

## DeepConsult

Source: **Table 4**. The reported [DeepConsult](https://github.com/youdotcom-oss/ydc-deep-research-evals) evaluation uses 102 queries, the shared Qwen3.5-27B backbone and a `gemini-2.5-pro` judge. Average score uses a 0–10 scale, with 5 representing a tie. NWR is calculated over non-tied outcomes.

| System | Overall win rate | Average score | NWR |
|---|---:|---:|---:|
| **VeriTrace** | **81.1%** | **6.94** | **0.887** |
| WebWeaver | 75.2% | 6.81 | 0.859 |
| FS-Researcher | 72.8% | 6.49 | 0.797 |
| Enterprise-DR | 64.7% | 6.11 | 0.769 |

## Model configurations

The [cost analysis](costs.md) reports LLM token costs for these configurations, including the paper's USD conversion and estimation limits. The project page provides an [interactive cost and quality explorer](https://wolfgaga.github.io/VeriTrace/#cost).

Source: **Table 1**. P/S means Planner/Searcher. The cognitive manager uses the P/S model in Qwen configurations and the Reader model in Config-DeepSeek.

| Configuration | Planner/Searcher | Reader | Writer |
|---|---|---|---|
| Config-3B | Qwen3.5-35B-A3B | Qwen3.5-35B-A3B | Qwen3.5-35B-A3B |
| Config-27B | Qwen3.5-27B | Qwen3.5-35B-A3B | Qwen3.5-27B |
| Config-Plus | Qwen3.5-Plus | Qwen3.5-122B-A10B | Qwen3.6-Plus |
| Config-DeepSeek | DeepSeek-V4-Pro | DeepSeek-V4-Flash | DeepSeek-V4-Pro |

Use [configuration](../VERITRACE/docs/configuration.md) and [evaluation](../VERITRACE/benchmark/DATA_README.md) for new experiments. Keep run outputs separate and report the actual model route, judge, prompt language and sample count. The demo is an interface demonstration, not a benchmark result.
