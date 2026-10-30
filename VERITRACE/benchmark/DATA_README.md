# Benchmark evaluation

VeriTrace exports research reports for FACT and RACE evaluation. The evaluation runner uses the sibling `deep_research_bench` directory:

Interactive and batch research use the complete DeepResearch workflow. Saved-evidence rewrites rerun the same outline, section-planning and section-writing stages without repeating retrieval.

```text
project/
|-- VERITRACE/
|   `-- benchmark/run_eval.py
`-- deep_research_bench/
    |-- deepresearch_bench_race.py
    |-- data/
    |-- prompt/
    `-- utils/
```

Install the evaluator dependencies from `deep_research_bench/requirements.txt` and configure its model and search credentials before running evaluation. Keep the benchmark query set, criteria, reference reports, judge model, and scoring configuration consistent when comparing systems.

## Generate reports

From `VERITRACE/`, run:

```shell
python benchmark/run_terminal.py --input ../deep_research_bench/data/prompt_data/query.jsonl --output eval_data/terminal/reports.jsonl --save-evidence
```

The runner writes JSONL records with query IDs, prompts, status, and report text. Saved evidence supports inspection and independent report writing.

## Evaluate reports

```shell
python benchmark/run_eval.py --input eval_data/terminal/reports.jsonl --name veritrace_example --race-only
python benchmark/run_eval.py --input eval_data/terminal/reports.jsonl --name veritrace_fact_example --fact-only
```

Omit `--race-only` and `--fact-only` to run both evaluations. The runner selects successful records with nonempty articles. Report the attempted and successfully scored query counts alongside aggregate scores.

Use a distinct experiment name for each independent run. Reusing a name can replace intermediate files. Evaluation calls configured external services and consumes provider quota.

For an OpenAI-compatible evaluation provider, set `GEMINI_BASE_URL` to that provider's compatible endpoint and `GEMINI_API_KEY` to its access token. `RACE_MODEL` selects the judge model or provider alias; it defaults to `gemini-2.5-pro`. `FACT_MODEL` defaults to `gemini-2.5-flash`. Keep these credentials in your local environment. Record the endpoint, requested model, and returned model when comparing scores, because a provider may redirect an alias to a different model. Re-score saved control reports through the same current judge when the scoring channel has changed.

The cleaner can retry long articles as contiguous chunks. The chunks cover the original text without gaps or overlaps. A failed chunk rejects that cleaning attempt rather than producing a partial article for scoring.

## Results and implementation

| Component | Location under `deep_research_bench/` |
|---|---|
| RACE evaluation | `deepresearch_bench_race.py` |
| Criterion and dimension weighting | `utils/score_calculator.py` |
| English and Chinese judging prompts | `prompt/score_prompt_en.py`, `prompt/score_prompt_zh.py` |
| Article cleaning | `utils/clean_article.py` |
| FACT citation extraction and checking | `utils/extract.py`, `utils/deduplicate.py`, `utils/scrape.py`, `utils/validate.py` |
| FACT score aggregation | `utils/stat.py` |
| RACE output | `results/race/<name>/` |
| FACT output | `results/fact/<name>/` |

RACE compares target and reference reports across comprehensiveness, insight, instruction following, and readability. Overall is `target_total / (target_total + reference_total)`, using weighted totals. It is not an accuracy percentage or the arithmetic mean of normalized dimension scores.

Retain query IDs, source reports, evaluation settings, and per-query results when preparing reproducible comparisons. See the evaluator's README for benchmark data and provider configuration.
