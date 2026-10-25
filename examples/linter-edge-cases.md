# 检查器边界用例

运行内置回归测试：

```bash
python3 scripts/zh-lint.py --selftest
```

## 应保持零告警

### 情态与状态

```text
请求可能已失败。任务尚未完成。
```

检查器不得建议删除“可能”或把“尚未完成”判为“失败”。

### 唯一指代

```text
读取配置。它会决定启动方式。
```

前句只有一个明确对象，不触发 `pronoun-reference`。

### 行内代码

````text
使用 `配置,测试` 作为标签。
````

行内代码中的 ASCII 逗号不触发 `ascii-comma`。

### 围栏代码

````markdown
```text
所有任务都不要重试。
```
````

代码块不触发语言检查。

### URL

```text
https://example.com/a;b。
```

URL 中的分号不触发 `multi-action`。

### 纯英文行

```text
Read config; then start the service.
```

逐行规则只检查含中日韩字符的行，英文分号与逗号不产生命中。

### 非散文行

````markdown
| 读取配置；启动服务 | 通过 |
| --- | --- |

> 读取配置；再启动服务。

    读取配置；启动服务。

[2026-10-25 10:00:25] 读取配置；启动服务。

{"note": "读取配置；启动服务"}
````

Markdown 表格、引用块、四空格缩进块、时间戳／日志行、裸 JSON 行都不触发逐行规则。

## 应触发指定规则

| 输入 | 规则 | 默认级别 |
|---|---|---|
| `请检查配置（见附录A)。` | `mixed-brackets` | error |
| `读取配置,启动服务。` | `ascii-comma` | error |
| `服务不可用。。请重试。` | `duplicate-punctuation` | error |
| `读取配置，然后启动服务。` | `multi-action-soft` | warning |
| `读取配置；启动服务。` | `multi-action` | warning |
| `所有任务都不要重试。` | `negation-scope` | warning |
| `超时 30 秒以上的任务会终止。` | `quantity-range` | warning |
| `将配置写入缓存和数据库。` 后另起一行 `它会被删除。` | `pronoun-reference` | warning |

所有 warning 都需要复核。命中不等于原句一定有歧义；未命中也不等于原句清楚。

`multi-action` 在 `doc` 与 `spec` 档报告；`multi-action-soft` 只在 `spec` 档报告；`chat` 档两个都不报。

## 术语表

给定 `terms.json`：

```json
{
  "任务": ["作业"]
}
```

输入：

```text
创建任务后，系统返回作业编号。
```

应触发一次 `terminology`，提示“任务”和“作业”混用。该检查只在用户明确配置术语关系后运行。

## 档位

默认是 `doc`。同一段文本在不同档位下的结果不同：

| 文本 | chat | doc（默认） | spec |
|---|---|---|---|
| `读取配置；启动服务。` | 不报 | 1 条 `multi-action` | 1 条 `multi-action` |
| `第一项；第二项；第三项。` | 不报 | 1 条（每行去重） | 2 条（每个分号一条） |
| `读取配置，然后启动服务。` | 不报 | 不报 | 1 条 `multi-action-soft` |

```bash
python3 scripts/zh-lint.py chat.md --profile chat
python3 scripts/zh-lint.py spec.md --profile spec
```

## 退出码

- 只有 warning：退出码为 0。
- error 数量大于 `--baseline`：退出码为 1。
- 文件或术语表无法读取、参数错误：退出码为 2。
- `--disable` 在 profile 之上再关闭规则；例如 `--profile spec --disable multi-action-soft` 只保留强信号，但仍按每个分号报（不去重）。

## 尚不自动检查

以下问题仍由模型结合上下文检查，不能靠本脚本作出结论：

- 零主语或零宾语是否真的产生歧义；
- “和、或、及”的真实并列层级；
- 多重“的”字结构的真实切分；
- 施事与受事关系；
- 条件、例外和否定词的真实语义范围；
- 改写是否保留事实、情态、时间和责任主体。
