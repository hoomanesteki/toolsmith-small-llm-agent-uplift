| Configuration | Abstain recall | Over-refusal | Injection resisted | Unsanctioned actions | Citation recall |
| --- | ---: | ---: | ---: | ---: | ---: |
| Oracle (ceiling) | 1.000 | 0.000 | 1.000 | 0.000 | 1.000 |
| Cascade (gpt-5.4-mini executor) | 0.968 | 0.000 | 0.600 | 0.000 | 1.000 |
| All frontier (Opus 5) | 1.000 | 0.000 | 1.000 | 0.000 | 1.000 |
| Cascade (frontier verifier) | 1.000 | 0.000 | 0.400 | 0.000 | 1.000 |
| Ablation: no compaction | 0.903 | 0.000 | 0.400 | 0.000 | 0.941 |
| Ablation: all tools in prompt | 0.903 | 0.000 | 0.400 | 0.000 | 0.941 |
| Cascade (Qwen executor, OSS verifier) | 0.903 | 0.000 | 0.400 | 0.000 | 0.941 |
| Single model (Sonnet 5) | 0.839 | 0.000 | 0.800 | 0.000 | 1.000 |
| Ablation: executor reviews itself | 0.903 | 0.000 | 0.400 | 0.000 | 1.000 |
| Cascade (fully open weights) | 0.968 | 0.000 | 0.300 | 0.000 | 0.941 |
| Ablation: no escalation | 0.806 | 0.000 | 0.400 | 0.000 | 0.824 |
| Naive role split (frontier bookends) | 0.806 | 0.000 | 0.300 | 0.000 | 0.941 |
| All cheap (gpt-oss-20b) | 0.871 | 0.000 | 0.500 | 0.000 | 0.882 |
| Local only (Qwen3.5-4B, 4-bit) | 0.613 | 0.000 | 0.200 | 0.000 | 0.647 |
| Coin flip (floor) | 0.387 | 0.000 | 0.100 | 0.000 | 0.059 |
