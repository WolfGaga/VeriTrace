// Paper Tables 2 and 15; USD estimates use 7.2 CNY/USD and May 2026 uncached rates.
export const configurations = [
  {
    id: "27b", label: "Config-27B", short: "27B", usd: 0.63, cny: 4.56,
    overall: 52.60, insight: 56.12, calls: 212, inputK: 1592, outputK: 1106,
    searches: 47.31, minutes: 57.41,
    note: "The configuration used in the paper's matched-backbone comparison.",
    roles: "Qwen3.5-27B plans, searches, manages the graph and writes. Qwen3.5-35B-A3B reads and filters sources.",
  },
  {
    id: "3b", label: "Config-3B", short: "3B", usd: 0.74, cny: 5.35,
    overall: 51.96, insight: 55.22, calls: 260, inputK: 2261, outputK: 1391,
    searches: 57.05, minutes: 45.80,
    note: "A single model family across all research and writing roles.",
    roles: "Qwen3.5-35B-A3B handles all roles. Its reported run uses more tokens than Config-27B, despite the smaller active model size.",
  },
  {
    id: "plus", label: "Config-Plus", short: "Plus", usd: 1.39, cny: 9.98,
    overall: 53.50, insight: 57.08, calls: 264, inputK: 2224, outputK: 1348,
    searches: 59.45, minutes: 64.78,
    note: "A larger Qwen configuration with a separate writing model.",
    roles: "Qwen3.5-Plus plans, searches and manages the graph; Qwen3.5-122B-A10B reads; Qwen3.5-35B-A3B filters; Qwen3.6-Plus writes.",
  },
  {
    id: "deepseek", label: "Config-DeepSeek", short: "DeepSeek", usd: 1.96, cny: 14.08,
    overall: 55.77, insight: 59.56, calls: 225, inputK: 1750, outputK: 461,
    searches: 46.59, minutes: 40.61,
    note: "The highest RACE Overall score in the paper's configuration comparison.",
    roles: "DeepSeek-V4-Pro plans, searches and writes. DeepSeek-V4-Flash handles graph updates, reading and source filtering.",
  },
];
