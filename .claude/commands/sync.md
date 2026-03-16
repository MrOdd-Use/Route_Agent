# 审查、提交并同步工作区

按以下流程执行，每一步必须完成后再进入下一步。

用户参数: $ARGUMENTS（可选的 commit message）

## 第一步：审查工作区

并行执行以下命令，了解当前工作区状态：

1. `git status` — 查看所有变更文件（不使用 -uall 标志）
2. `git diff` — 查看未暂存的变更内容
3. `git diff --cached` — 查看已暂存的变更内容
4. `git log --oneline -5` — 查看最近提交风格

## 第二步：展示变更摘要

向用户展示以下信息：

- 变更文件列表（新增、修改、删除）
- 每个文件的变更概要（改了什么、为什么）
- 是否有不应该提交的文件（.env、credentials、大文件等）

## 第三步：生成 commit message

根据变更内容生成 commit message：

- 遵循 conventional commits 格式：`<type>: <description>`
- type 包括：feat, fix, refactor, docs, test, chore, perf, ci
- 描述简洁准确，1-2 句话，关注"为什么"而不是"做了什么"
- 如果用户通过参数提供了 commit message（`$ARGUMENTS`），优先使用用户提供的

展示完整的 commit 计划（要暂存哪些文件、commit message 是什么），等待用户确认。

## 第四步：执行提交和推送

用户确认后，执行：

1. `git add <具体文件>` — 按文件名暂存，不使用 `git add -A` 或 `git add .`
2. `git commit` — 使用生成的 commit message，通过 HEREDOC 传递
3. `git status` — 验证提交成功
4. `git push` — 推送到远程（如果是新分支，使用 `-u` 标志）
5. 展示最终结果：commit hash 和远程同步状态

## 安全规则

- 不提交可能包含密钥的文件（.env, credentials.json, *.pem 等），发现时警告用户
- 不使用 `--force` 推送
- 不使用 `--no-verify` 跳过 hooks
- 不修改 git config
- 每一步有副作用的操作都需要用户确认后再执行
