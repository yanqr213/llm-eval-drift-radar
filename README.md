# llm-eval-drift-radar

`llm-eval-drift-radar` 是一个零运行时依赖的 Python CLI，用来比较两次 LLM eval 结果，定位用例级回归、分类/标签漂移、模型版本变化、评分波动、延迟和成本回归，并生成可以进入 PR 评论、CI artifact 或测试结果面板的 Markdown / JSON / CSV / JUnit XML 报告。

它面向已经在跑 LLM eval 的开发者和团队：你可能每天在 CI 里比较 main 分支与当前 PR，或者在模型升级、prompt 调整、RAG 索引更新后确认有没有隐藏退化。这个工具只读取本地文件，不联网，不读取 GitHub token，也不推送任何内容。

## 功能

- 读取 baseline/current 的 JSONL 或 CSV eval 结果。
- 按 `case_id` 对齐，识别新增、缺失和共同用例。
- 检测新失败、修复、分数下降、类别/标签漂移、模型变化、延迟回归、成本回归。
- 支持 thresholds JSON 配置阈值和 CI 失败策略。
- 输出 Markdown、JSON、CSV、JUnit XML。
- `--check` 模式可在发现配置的失败条件时返回退出码 `1`，适合 CI gating。
- 只使用 Python 标准库，离线可运行。

## 安装

从源码安装：

```bash
python -m pip install .
```

不安装也可以直接运行：

```bash
PYTHONPATH=src python -m llm_eval_drift_radar --help
```

Windows PowerShell：

```powershell
$env:PYTHONPATH="src"
python -m llm_eval_drift_radar --help
```

## 输入格式

支持 `.jsonl` 和 `.csv`。每行或每条记录代表一个 eval case。只有 `case_id` 是必填字段，其他字段缺失时会跳过对应检测。

推荐字段：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `case_id` | string | 稳定唯一 ID，用于 baseline/current 对齐 |
| `prompt` | string | 输入 prompt 或测试描述 |
| `expected` | string | 期望答案、断言或 rubric |
| `actual` | string | 模型实际输出 |
| `score` | number | 评分，通常为 0 到 1 |
| `pass` | boolean | 是否通过，可用 `true/false`、`yes/no`、`pass/fail` |
| `category` | string | 标签、场景、风险分类 |
| `model` | string | 模型名称或版本 |
| `latency_ms` | number | 端到端或模型调用延迟，毫秒 |
| `cost_usd` | number | 该 case 的估算成本，美元 |

JSONL 示例：

```json
{"case_id":"qa_refund_001","prompt":"用户问：退款多久到账？","expected":"说明退款一般 3-7 个工作日到账。","actual":"退款一般 3-7 个工作日到账。","score":0.96,"pass":true,"category":"policy/refund","model":"gpt-4.1-mini-2026-04","latency_ms":820,"cost_usd":0.0012}
```

CSV 示例：

```csv
case_id,prompt,expected,actual,score,pass,category,model,latency_ms,cost_usd
qa_refund_001,Refund timing?,3-7 business days,Depends on bank,0.74,false,policy/refund,gpt-4.1-mini-2026-05,990,0.0014
```

## 命令示例

生成 Markdown 报告到 stdout：

```bash
python -m llm_eval_drift_radar \
  --baseline examples/baseline.jsonl \
  --current examples/current.jsonl
```

生成 JSON 报告：

```bash
python -m llm_eval_drift_radar \
  --baseline examples/baseline.jsonl \
  --current examples/current.jsonl \
  --format json \
  --output report.json
```

生成 CSV 明细：

```bash
python -m llm_eval_drift_radar \
  --baseline examples/baseline.csv \
  --current examples/current.csv \
  --format csv \
  --output report.csv
```

生成 JUnit XML，方便 CI 展示失败用例：

```bash
python -m llm_eval_drift_radar \
  --baseline examples/baseline.jsonl \
  --current examples/current.jsonl \
  --thresholds examples/thresholds.json \
  --format junit \
  --output llm-eval-drift-report.xml
```

CI check 模式：

```bash
python -m llm_eval_drift_radar \
  --baseline examples/baseline.jsonl \
  --current examples/current.jsonl \
  --thresholds examples/thresholds.json \
  --check \
  --output llm-eval-drift-report.md
```

退出码：

- `0`: 命令成功，且 `--check` 没有发现配置为失败的条件。
- `1`: 命令成功，但 `--check` 发现配置为失败的条件。
- `2`: 输入、解析或参数错误。

## thresholds JSON

默认配置：

```json
{
  "score_drop": 0.05,
  "latency_regression_pct": 20.0,
  "cost_regression_pct": 20.0,
  "fail_on_new_failures": true,
  "fail_on_score_drops": true,
  "fail_on_category_drift": false,
  "fail_on_model_changes": false,
  "fail_on_latency_regressions": false,
  "fail_on_cost_regressions": false,
  "fail_on_missing_cases": false,
  "fail_on_new_cases": false
}
```

说明：

- `score_drop`: 当前分数相对 baseline 下降达到该绝对值即标记 `score_drop`。例如 baseline `0.90`、current `0.82`、阈值 `0.05` 会触发。
- `latency_regression_pct`: 当前延迟相对 baseline 增长达到百分比即标记。
- `cost_regression_pct`: 当前成本相对 baseline 增长达到百分比即标记。
- `fail_on_*`: 控制 `--check` 模式下哪些信号会导致退出码 `1`。

## 输出格式

Markdown 报告包含 summary 和 case-level table，适合直接上传为 CI artifact 或贴到 PR 评论。

JSON 报告包含：

- `summary`: 聚合计数、通过率、平均分、总成本、失败原因。
- `thresholds`: 实际生效阈值。
- `cases`: 每个 `case_id` 的 baseline/current 快照、状态和 delta。

CSV 报告是一行一个 case，适合导入表格或 BI。

JUnit XML 报告是一条 eval case 对应一个 testcase。只有 thresholds 中配置为失败策略的信号会生成 `<failure>`，因此它和 `--check` 的判断语义一致，适合上传为 CI 测试结果或 artifact。

## GitHub Actions / CI

仓库内置 `.github/workflows/ci.yml`，会运行单元测试和示例 check。你可以在自己的 eval 流程中先产出两份结果文件，再调用：

```yaml
- name: Compare LLM eval drift
  run: |
    python -m llm_eval_drift_radar \
      --baseline artifacts/baseline.jsonl \
      --current artifacts/current.jsonl \
      --thresholds examples/thresholds.json \
      --format markdown \
      --output llm-eval-drift-report.md
    python -m llm_eval_drift_radar \
      --baseline artifacts/baseline.jsonl \
      --current artifacts/current.jsonl \
      --thresholds examples/thresholds.json \
      --format junit \
      --output llm-eval-drift-report.xml
    python -m llm_eval_drift_radar \
      --baseline artifacts/baseline.jsonl \
      --current artifacts/current.jsonl \
      --thresholds examples/thresholds.json \
      --quiet \
      --check

- name: Upload drift report
  if: always()
  uses: actions/upload-artifact@v4
  with:
    name: llm-eval-drift-report
    path: |
      llm-eval-drift-report.md
      llm-eval-drift-report.xml
```

如果你希望模型变化或类别漂移也阻塞 PR，把 thresholds 里的 `fail_on_model_changes` 或 `fail_on_category_drift` 设为 `true`。

## 隐私与安全边界

- 工具只读取你显式传入的 baseline/current/thresholds 文件。
- 工具不访问网络，不调用模型 API，不读取环境变量中的 GitHub token 或其他密钥。
- 工具不会推送 GitHub，也不会修改远程仓库。
- 报告会包含 prompt、expected、actual 等输入文件中的内容；如果这些字段含有敏感数据，请在生成 report 前脱敏，或只输出 JSON/CSV 后在内部系统处理。
- `--output` 只写入你指定的本地路径。

## 开发

运行测试：

```bash
python -m unittest discover -s tests
```

运行示例：

```bash
PYTHONPATH=src python -m llm_eval_drift_radar \
  --baseline examples/baseline.jsonl \
  --current examples/current.jsonl \
  --thresholds examples/thresholds.json \
  --format markdown
```

## English

`llm-eval-drift-radar` is a dependency-free Python CLI for comparing two LLM evaluation runs. It aligns records by `case_id`, detects case-level regressions and drift, and emits Markdown, JSON, CSV, or JUnit XML reports that can be used in pull requests and CI pipelines.

### Who It Is For

This project is for developers and teams that already run LLM evals and need a reliable way to compare a baseline run against a current run. Common use cases include prompt changes, model upgrades, retrieval/index changes, tool-call changes, or PR-level eval gating.

### Features

- Reads baseline/current eval results from JSONL or CSV.
- Aligns cases by stable `case_id`.
- Detects new failures, fixes, score drops, category drift, model changes, latency regressions, cost regressions, new cases, and missing cases.
- Supports a thresholds JSON file for regression thresholds and CI failure policy.
- Outputs Markdown, JSON, CSV, or JUnit XML.
- Provides `--check` mode with CI-friendly exit codes.
- Uses only the Python standard library and works offline.

### Install And Run

Install from source:

```bash
python -m pip install .
```

Run without installing:

```bash
PYTHONPATH=src python -m llm_eval_drift_radar --help
```

Generate a Markdown report:

```bash
python -m llm_eval_drift_radar \
  --baseline examples/baseline.jsonl \
  --current examples/current.jsonl \
  --thresholds examples/thresholds.json \
  --format markdown
```

Generate JUnit XML for CI test views:

```bash
python -m llm_eval_drift_radar \
  --baseline examples/baseline.jsonl \
  --current examples/current.jsonl \
  --thresholds examples/thresholds.json \
  --format junit \
  --output llm-eval-drift-report.xml
```

Use check mode in CI:

```bash
python -m llm_eval_drift_radar \
  --baseline artifacts/baseline.jsonl \
  --current artifacts/current.jsonl \
  --thresholds examples/thresholds.json \
  --output llm-eval-drift-report.md \
  --check
```

Exit codes:

- `0`: success, and no configured failure condition in `--check` mode.
- `1`: success, but configured failure conditions were found in `--check` mode.
- `2`: input, parsing, or argument error.

JUnit XML maps each eval case to one testcase. Only signals enabled by the thresholds failure policy are emitted as `<failure>` nodes, so the XML failure count matches the same policy used by `--check`.

### Input Schema

Required field:

- `case_id`: stable unique case identifier used for alignment.

Recommended fields:

- `prompt`: prompt or test description.
- `expected`: expected answer, assertion, or rubric.
- `actual`: model output.
- `score`: numeric score.
- `pass`: boolean pass/fail value.
- `category`: scenario, tag, or risk category.
- `model`: model name or version.
- `latency_ms`: latency in milliseconds.
- `cost_usd`: estimated cost for the case.

### Privacy And Security

The tool only reads files you explicitly pass in. It does not access the network, does not call model APIs, does not read GitHub tokens or other secrets from the environment, and does not push to GitHub. Reports may include sensitive eval content from your input files, so sanitize inputs before publishing reports outside your trusted environment.
