# 计划：统一升阶目标遍历逻辑

## Context

现有 `escalate_with_overload_check_async` 存在两个问题：

1. **只检查一个目标**，目标过载时直接退回 retry 或 alert，没有尝试候选列表中其他更强的模型
2. **执行失败触发升阶、但目标过载时，错误地返回 `retry` 原模型**——原模型是因为执行失败才升阶的，retry 它没有意义

用户提出：统一升阶方式，按强度顺序最多尝试 3 个目标，全部不可用后：
- 质量失败触发的升阶 → retry 原模型（需先确认原模型仍可用）
- 执行失败触发的升阶 → 拉起警报（`alert_escalation_unavailable`）

---

## 修改文件

- `route_agent/router_engine/constants.py`
- `route_agent/router_engine/escalation.py`

---

## 实现步骤

### 1. constants.py：新增常量

```python
MAX_ESCALATION_ATTEMPTS: int = 3
```

### 2. escalation.py：重写 `escalate_with_overload_check_async`

**新逻辑：**

```
1. 调用 next_action(priority) 获取初步决策
2. 如果不是升阶类动作（retry / alert）→ 直接返回
3. 构建候选目标列表（按强度降序，最多取 MAX_ESCALATION_ATTEMPTS 个）：
   - 从 candidates[current_index-1] 向 candidates[0] 遍历
   - 若不足 3 个，追加 breakthrough_candidate()（如果存在）
4. 对每个目标依次检查：
   a. util.is_limited → 跳过
   b. peak >= 阈值（按 priority 决定阈值）→ 跳过
   c. is_escalation_capped → 跳过
   d. 通过 → 返回 escalate/escalate_breakthrough 到该目标
5. 所有目标均不可用时：
   - 判断最后一次 attempt 是否为执行失败（failure_type != "quality"）
   - 执行失败 → action="alert_escalation_unavailable", next_model=None
   - 质量失败 → 检查原模型当前是否仍可用（get_utilization_async 的 is_limited）
     - 原模型仍可用 → action="retry", next_model=current_model_id
     - 原模型也不可用 → action="alert_escalation_unavailable", next_model=None
```

**priority 阈值规则（保持不变）：**

| priority  | 跳过目标的条件                      |
|-----------|----------------------------------|
| `normal`  | peak >= max(0.90, 0.85) = 0.90   |
| `elevated`| peak >= 0.95                     |
| 其他       | 不做 peak 检查                    |

### 3. escalation.py：删除 `_find_uncapped_alternative`

该方法的职责完全被新循环吸收，可以删除。不影响任何外部调用（仅被 `escalate_with_overload_check_async` 内部调用）。

---

## 关键设计决策

- **`next_action` 不改动**：它负责判断"应该升阶还是重试"，新逻辑只修改"升阶时如何选目标"
- **MAX_ESCALATION_ATTEMPTS = 3**：用户指定，放入 constants 便于调整
- **失败类型判断放在兜底处**：循环结束后才区分 exec fail vs quality fail，逻辑集中清晰
- **兜底 retry 前需验证原模型**：质量失败路径返回 retry 前，用 `get_utilization_async` 检查原模型是否仍可用，避免退回一个已被限速的模型

---

## 验证

```bash
uv run pytest -v route_agent/router_engine/tests/
```

手动验证场景：
1. 所有目标过载 + 质量失败触发 → 返回 retry 原模型
2. 所有目标过载 + 执行失败触发 → 返回 alert_escalation_unavailable
3. 第 1 个目标过载、第 2 个可用 → 返回 escalate 到第 2 个目标
4. 候选列表不足 3 个、breakthrough 存在 → breakthrough 作为最后候选
5. 所有目标过载 + 质量失败 + 原模型也被限速 → 返回 alert_escalation_unavailable
