# controlled-chinese

面向智能体、下游系统或技术读者的中文受控改写 skill。入口是 [`SKILL.md`](SKILL.md)。

目标是消除指代、范围、条件和动作主体的歧义，不追求把中文改短或改硬。设计受 ASD-STE100 启发，但不是其翻译版，也不代表符合任何官方中文标准。

## 安装

目录方式：

```bash
cp -R controlled-chinese ~/.config/opencode/skills/
```

或解压发布包 `controlled-chinese.skill`（zip 格式）。

## 检查器

```bash
python3 scripts/zh-lint.py <file>
python3 scripts/zh-lint.py <file> --profile chat|doc|spec
```

| profile | 适用 | 多动作检查 | 每行去重 |
|---|---|---|---|
| `chat` | 聊天、复盘叙述 | 关 | — |
| `doc`（默认） | 长文、报告 | 只报分号 | 是 |
| `spec` | 规范、步骤清单 | 分号 + 连词 | 否 |

其他参数：`--json`、`--baseline N`、`--disable rule1,rule2`、`--glossary terms.json`。

## 自检

```bash
python3 scripts/zh-lint.py --selftest
```

## 结构

```text
SKILL.md                    入口：何时用、怎么改、边界
references/writing-rules.md 规则与改写方法
examples/                   正反例与检查器边界用例
scripts/zh-lint.py          机械检查器
```

## 维护

改规则后必须：`--selftest` 通过；同步 `references/writing-rules.md` 的规则表；在 `examples/linter-edge-cases.md` 增补边界用例。

## 许可证

MIT，见 [`LICENSE`](LICENSE)。
