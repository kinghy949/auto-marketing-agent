# Golden Cases

每个 JSON 文件是一条 canonical payload,对应某个 agent 的"已验证正确输出"样本。

## 用途

1. **Schema 回归**:任何一次 schema 字段改名 / 删除 / 约束变化,都要在这组测试里露头。
2. **Agent 行为锚点**:P0 阶段 agent 输出由人工写入;P2-010 后会用真实 LLM 输出替换,
   同步加 eval(语义相似度 / 结构命中率)。
3. **Eval Pipeline 阻塞门禁**(P2-012):CI 会在这组用例上跑 snapshot 对比。

## 目录

- `audience/*.json` —— `AudienceSegment` v1 样本
- `creative/*.json` —— `CreativeVariant` v1 样本

## 约束

- 命名用 `<id>_<场景>.json` 便于定位。
- 所有 `hashed_user_ids` 必须以 `h:` 开头,禁原始 PII。
- `license_id` / `licensor` 字段禁留空。
- 同一文件内 correlation_id / campaign_id 保持与文件名前缀一致,便于 cross-check。
