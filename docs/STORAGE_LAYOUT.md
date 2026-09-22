# 本地存储设计

```text
runtime_data/
├─ enterprise_source/          原始真实企业快照，只读
├─ enterprise_profiles/        当前运行画像、评价卡和版本
├─ enterprise_updates/         用户补充与变更记录
├─ decisions/                  投标决策历史
├─ competition_analysis/       竞争分析历史
├─ competition_demo/           明确标注的临时演示竞争者
├─ agent_runs/                 Agent 运行轨迹
├─ checkpoints/                SQLite/检查点
├─ monitoring_plans/           项目监控计划
├─ raw_api_responses/          非敏感原始响应引用/快照
└─ audit/                      JSONL 审计和错误记录
```

## 写入规则

1. `enterprise_source` 永不被应用写回；Windows 启动时再次设置只读属性。
2. 用户补充进入 `enterprise_updates`，投标资格补充直接用于对应任务核验；当前画像仍写入 `enterprise_profiles`。
3. 决策和竞争分析按任务 ID 独立保存，重启后仍可读取。
4. 所有持久化记录绑定企业 ID、项目 ID、数据时间、模型和规则版本。
5. API Key、数据库密码、SSH 密码、Authorization 等敏感字段在持久化前剔除。
6. 演示竞争数据不写入远程数据库，不与真实关系结果合并。
