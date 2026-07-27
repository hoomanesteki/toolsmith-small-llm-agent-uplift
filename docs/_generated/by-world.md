| Configuration | clinic | doc | ops | ops to clinic |
| --- | ---: | ---: | ---: | ---: |
| Oracle (ceiling) | 1.000 | 1.000 | 1.000 | +0.0 pp |
| Cascade (gpt-5.4-mini executor) | 0.961 | 0.946 | 0.960 | +0.1 pp |
| All frontier (Opus 5) | 0.941 | 0.946 | 0.960 | -1.8 pp |
| Cascade (frontier verifier) | 0.882 | 0.927 | 0.932 | -5.0 pp |
| Ablation: no compaction | 0.843 | 0.909 | 0.892 | -4.9 pp |
| Ablation: all tools in prompt | 0.843 | 0.909 | 0.892 | -4.9 pp |
| Cascade (Qwen executor, OSS verifier) | 0.843 | 0.909 | 0.892 | -4.9 pp |
| Single model (Sonnet 5) | 0.882 | 0.891 | 0.878 | +0.4 pp |
| Ablation: executor reviews itself | 0.784 | 0.891 | 0.824 | -4.0 pp |
| Cascade (fully open weights) | 0.784 | 0.818 | 0.865 | -8.1 pp |
| Ablation: no escalation | 0.686 | 0.746 | 0.676 | +1.1 pp |
| Naive role split (frontier bookends) | 0.667 | 0.545 | 0.689 | -2.3 pp |
| All cheap (gpt-oss-20b) | 0.745 | 0.527 | 0.608 | +13.7 pp |
| Local only (Qwen3.5-4B, 4-bit) | 0.412 | 0.400 | 0.486 | -7.5 pp |
| Coin flip (floor) | 0.039 | 0.000 | 0.054 | -1.5 pp |
