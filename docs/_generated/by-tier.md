| Configuration | T1 | T2 | T3 | T4 | T5 |
| --- | ---: | ---: | ---: | ---: | ---: |
| Oracle (ceiling) | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 |
| Cascade (gpt-5.4-mini executor) | 0.974 | 0.947 | 0.972 | 0.968 | 0.882 |
| All frontier (Opus 5) | 0.974 | 0.965 | 0.917 | 0.968 | 0.882 |
| Cascade (frontier verifier) | 0.974 | 0.877 | 0.833 | 1.000 | 0.941 |
| Ablation: no compaction | 0.949 | 0.877 | 0.806 | 0.903 | 0.882 |
| Ablation: all tools in prompt | 0.949 | 0.877 | 0.806 | 0.903 | 0.882 |
| Cascade (Qwen executor, OSS verifier) | 0.949 | 0.877 | 0.806 | 0.903 | 0.882 |
| Single model (Sonnet 5) | 0.872 | 0.895 | 0.917 | 0.839 | 0.882 |
| Ablation: executor reviews itself | 0.897 | 0.825 | 0.750 | 0.871 | 0.824 |
| Cascade (fully open weights) | 0.949 | 0.754 | 0.778 | 0.935 | 0.706 |
| Ablation: no escalation | 0.795 | 0.719 | 0.583 | 0.742 | 0.588 |
| Naive role split (frontier bookends) | 0.846 | 0.509 | 0.583 | 0.742 | 0.529 |
| All cheap (gpt-oss-20b) | 0.692 | 0.526 | 0.667 | 0.839 | 0.294 |
| Local only (Qwen3.5-4B, 4-bit) | 0.513 | 0.439 | 0.361 | 0.516 | 0.294 |
| Coin flip (floor) | 0.000 | 0.000 | 0.000 | 0.194 | 0.000 |
