# Career Job Hunter

> 本地可搭建的 LinkedIn 职位检索工具：零付费起步 · 按目标国家 / 技能 / 职位配置 · AI 分析后输出一目了然的 HTML 报告

---

## 📌 项目简介 (Overview)

**Career Job Hunter** 是一个可配置的 LinkedIn 职位检索与分析工具。你在 `config.json` 中设定搜索关键词、目标国家、工作类型和个人履历，工具会抓取公开岗位、用本地大模型打分，并生成 HTML 报告。

**核心优势：**

1. **零费用、本地搭建** — AI 分析跑在本机 [Ollama](https://ollama.com/) 上，不购买云端大模型或商业求职服务；数据源可用 Apify / RapidAPI 免费额度，在自己电脑上就能跑通全流程。
2. **按自己的需求配置** — 目标国家、个人 skills、目标职位、工作类型（On-site / Hybrid / Remote）都写在 `config.json` 里，换方向只改配置，不必改代码。仓库默认值只是示例，不绑定任何特定技术方向。
3. **AI 智能分析，结果一目了然** — 自动对照履历提取技术栈、签证 / Relocation 信息并打 1–10 分，输出按国家分组的 HTML 报告，打开即可扫过高分岗位。

它能够：

1. **抓取职位** — 通过 [Apify](https://apify.com/curious_coder/linkedin-jobs-scraper)（主）或 [RapidAPI](https://rapidapi.com/fantastic-jobs-fantastic-jobs-default/api/linkedin-job-search-api)（备）检索 LinkedIn 公开岗位
2. **按配置过滤** — 根据你设定的关键词、国家、工作类型（On-site / Hybrid / Remote）筛选结果
3. **AI 分析** — 调用本地 [Ollama](https://ollama.com/) 大模型，对照你的履历提取技术栈、签证/Relocation 信息，并给出 1–10 匹配度评分
4. **生成报告** — 输出按国家分组的 `report_YYYY-MM-DD.html`，优先展示高分且符合你偏好的岗位

**核心特性：**

| 特性 | 说明 |
|------|------|
| 双数据源 Failover | Apify 失败自动切换 RapidAPI |
| 省流量模式 | 默认每次运行仅 1 次 API 请求 |
| 本地二次过滤 | 按 `config.json` 中的关键词/正则筛选抓取结果，不额外消耗 API |
| 去重存储 | SQLite 记录已分析 `job_id`，避免重复消耗 AI 算力 |
| 额度保护 | RapidAPI 每月请求计数，超额自动停用 |

---

## 🛠️ 准备工作与账号注册 (Prerequisites & API Setup)

### 系统要求

- **Python 3.9+**
- 可访问互联网的终端环境
- 约 8 GB 可用磁盘空间（用于 Ollama 模型）

---

### 选项 A：Apify 账号（推荐 · 数据源 1）

Apify 是本项目**默认主数据源**，通过 [curious_coder/linkedin-jobs-scraper](https://apify.com/curious_coder/linkedin-jobs-scraper) Actor 抓取 LinkedIn 公开搜索页。

| 项目 | 详情 |
|------|------|
| 官网 | [Apify Console](https://console.apify.com/) |
| Actor 页面 | [curious_coder/linkedin-jobs-scraper](https://apify.com/curious_coder/linkedin-jobs-scraper) |

**注册与 Token 获取步骤：**

1. 访问 [Apify Console](https://console.apify.com/) 并注册免费账号
2. 登录后，点击左下角 **头像**
3. 进入 **Account settings → Integrations → API Tokens**
4. 复制 **Default Token**（格式类似 `apify_api_xxxxxxxx`）
5. 在项目根目录设置环境变量（见下方 [Configuration](#-环境变量与配置文件设置-configuration)）

**费用说明：**

- 免费账号每月赠送 **$5 USD** 平台额度
- 在 `scrape_company: false` 的默认配置下，足够抓取 **数百个职位**
- 建议保持 `count_per_search: 20`，避免单次消耗过多

---

### 选项 B：RapidAPI 账号（备用 · 数据源 2）

RapidAPI 作为 **Failover 备用数据源**。当 Apify 报错、超时或额度耗尽时，程序会自动切换。

| 项目 | 详情 |
|------|------|
| 官网 | [RapidAPI Marketplace](https://rapidapi.com/) |
| API 页面 | [LinkedIn Job Search API on RapidAPI](https://rapidapi.com/fantastic-jobs-fantastic-jobs-default/api/linkedin-job-search-api) |

**注册与 Key 获取步骤：**

1. 访问 [RapidAPI](https://rapidapi.com/) 并注册账号
2. 打开 [LinkedIn Job Search API](https://rapidapi.com/fantastic-jobs-fantastic-jobs-default/api/linkedin-job-search-api) 页面
3. 点击 **Subscribe to Test**
4. 选择 **Basic (Free)** 免费套餐并完成订阅
5. 在 API 页面的 **Header Parameters** 区域，复制 `x-rapidapi-key` 的值
6. 设置为环境变量 `RAPIDAPI_KEY`

**费用说明：**

- **Basic Plan** 每月包含 **25 次免费 Requests**
- 单次 Request 最多可拉取 **1000 条**职位（高吞吐设计）
- 项目内置 `rapidapi_quota.json` 计数器，默认每月最多使用 **20 次**（留 5 次余量）

> 💡 **建议：** 同时配置 Apify + RapidAPI，获得最佳稳定性。仅配置 Apify 也可正常运行。

---

### 选项 C：本地大模型环境（AI 总结模块）

AI 分析模块通过 **Ollama** 在本地运行，完全免费、数据不出本机。

| 项目 | 详情 |
|------|------|
| 下载地址 | [Ollama 官网](https://ollama.com/) |
| 推荐模型 | `deepseek-r1:8b`（默认）或 `llama3` |

**安装与启动步骤：**

1. 从 [ollama.com](https://ollama.com/) 下载并安装 Ollama
2. 拉取并运行模型（二选一）：

```bash
# 推荐 — 推理能力强，适合结构化 JSON 输出
ollama run deepseek-r1:8b

# 备选 — 通用模型，速度较快
ollama run llama3
```

3. 保持 Ollama 服务运行（安装后通常自动启动）
4. 验证服务可用：

```bash
curl http://localhost:11434/api/tags
```

**费用说明：** 完全免费，离线可用，无 API 调用限制。

---

## 🔑 环境变量与配置文件设置 (Configuration)

### 环境变量

在项目根目录创建 `.env` 文件（**请勿提交到 Git**）：

```env
# 必填 — Apify 主数据源
APIFY_TOKEN=apify_api_your_token_here

# 可选 — RapidAPI 备用数据源
RAPIDAPI_KEY=your_rapidapi_key_here

# 可选 — 搜索模式：SAVE（默认省流量）| FULL（全量 URL 组合）
# SEARCH_MODE=FULL
```

加载环境变量：

```bash
# macOS / Linux
set -a && source .env && set +a

# 或手动 export
export APIFY_TOKEN="apify_api_your_token_here"
export RAPIDAPI_KEY="your_rapidapi_key_here"
```

| 变量 | 必填 | 说明 |
|------|------|------|
| `APIFY_TOKEN` | 推荐 | Apify API Token |
| `RAPIDAPI_KEY` | 可选 | RapidAPI Key（Failover 备用） |
| `SEARCH_MODE` | 可选 | `FULL` = 全量搜索；未设置 = 省流量模式 |

---

### `config.json` 关键配置

编辑项目根目录的 `config.json`。下面的关键词、国家和履历都是**示例**，请改成你自己的求职方向。

#### 数据源

```json
{
  "primary_source": "apify",
  "apify": {
    "actor_id": "curious_coder/linkedin-jobs-scraper",
    "token_env_var": "APIFY_TOKEN",
    "count_per_search": 20,
    "scrape_company": false
  }
}
```

| 字段 | 说明 |
|------|------|
| `primary_source` | `"apify"` 或 `"rapidapi"` — 优先使用的数据源 |
| `apify.count_per_search` | 每次 Apify 运行最多抓取职位数（建议 20） |
| `apify.scrape_company` | 保持 `false` 可显著提速并节省 Apify 额度 |

#### 搜索参数

以下 JSON 仅为示例。把 `keywords` / `core_keywords` 换成你要搜的岗位即可。未配置 `local_filter.patterns` 时，本地二次过滤会跳过，保留全部抓取结果。

```json
{
  "search_params": {
    "keywords": ["Cloud Infrastructure", "Kubernetes", "DevOps", "Software Engineer"],
    "core_keywords": ["Cloud Infrastructure", "Kubernetes", "DevOps"],
    "locations": ["Germany", "United States", "United Kingdom", "Australia", "New Zealand", "Canada", "Ireland"],
    "allow_remote": false,
    "work_types": ["on_site", "hybrid"],
    "target_relocation": true,
    "fetch_limit_per_request": 1000
  }
}
```

| 字段 | 说明 |
|------|------|
| `keywords` | LinkedIn 搜索关键词（`SEARCH_MODE=FULL` 时使用），按你的目标岗位填写 |
| `core_keywords` | 省流量模式随机抽取的关键词池 |
| `locations` | 目标国家列表（使用英文全称，如 `"United States"`） |
| `allow_remote` | `false` = 排除纯 Remote 岗位（示例默认）；`true` = 包含 Remote |
| `work_types` | 允许的工作类型：`on_site` · `hybrid` · `remote` |
| `target_relocation` | `true` = AI 优先推荐支持 Relocation / Visa 的实地岗位 |
| `local_filter.patterns` | 本地二次过滤的关键词/正则，只保留标题或描述命中的职位 |
| `fetch_limit_per_request` | RapidAPI 单次请求最大职位数（上限 1000） |

> 当 `allow_remote: false` 时，LinkedIn 搜索 URL 会自动追加 `&f_WT=1,3`（On-site + Hybrid 过滤器）。

#### 个人履历（AI 打分依据）

```json
{
  "user_profile": {
    "summary": "Your background and job search goal...",
    "skills": ["Go", "Python", "Kubernetes", "Terraform", "AWS"],
    "preferences": {
      "visa_sponsorship": true,
      "remote": false,
      "target_roles": ["Cloud Engineer", "DevOps Engineer", "SRE"]
    }
  }
}
```

修改 `summary`、`skills` 和 `preferences`，使 AI 评分对照你的背景与目标岗位，而不是某个固定技术方向。

---

## 🚀 快速开始与运行命令 (Quick Start)

### 1. 克隆项目并创建虚拟环境

```bash
git clone <your-repo-url> career-job-hunter
cd career-job-hunter

python3 -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
```

### 2. 安装依赖

```bash
pip install -r requirements.txt
```

> 本项目仅使用 Python 标准库，无需安装第三方包。上述命令用于保持流程一致性。

### 3. 配置 API Key 与个人资料

```bash
cp config.json config.json.bak   # 可选：备份默认配置
# 编辑 config.json — 按你的目标岗位修改 keywords、locations、local_filter、user_profile
# 创建 .env 并填入 APIFY_TOKEN（及可选的 RAPIDAPI_KEY）
set -a && source .env && set +a
```

### 4. 启动 Ollama（如尚未运行）

```bash
ollama run deepseek-r1:8b
```

### 5. 运行主程序

```bash
# 默认省流量模式 — 随机 1 关键词 + 1 国家，1 次 API 请求
python main.py
```

```bash
# 全量模式 — 遍历所有 keywords × locations 组合（仍仅 1 次 API 调用）
SEARCH_MODE=FULL python main.py
```

### 运行输出

| 文件 | 说明 |
|------|------|
| `jobs.db` | SQLite 数据库，存储已分析职位 |
| `report_YYYY-MM-DD.html` | 当日 HTML 报告（用浏览器打开） |
| `rapidapi_quota.json` | RapidAPI 月度请求计数（自动生成） |

运行结束后，终端会打印报告路径。用浏览器打开即可查看：

```bash
# macOS
open report_YYYY-MM-DD.html

# Linux
xdg-open report_YYYY-MM-DD.html

# Windows（PowerShell / CMD）
start report_YYYY-MM-DD.html
```

也可以直接双击该文件，或把它拖进 Chrome / Safari / Firefox。把 `YYYY-MM-DD` 换成当天日期，例如 `report_2026-08-24.html`。

### Example output

一次典型运行（省流量模式，1 次 API 请求）的终端输出如下。本地路径已脱敏。

```text
=== Step 1: Fetching jobs ===
Strategy: single bulk request per run (maximize jobs, minimize API calls)
Primary source: apify
Search mode: SAVE
LinkedIn search URLs: 1
  - https://www.linkedin.com/jobs/search/?keywords=Cloud%20Infrastructure&location=Germany&f_WT=1,3
Apify run_input: count=20, scrapeCompany=False, allow_remote=False, f_WT=1,3
Failover order: apify
RapidAPI quota: 0/20 used this month
  Note: some configured sources were skipped due to missing credentials or quota.
Sending 1 API request...
  Source 'apify' returned 20 raw job(s) in 1 request
Local filtering 20 raw job(s) (no extra network requests)...
Local filter matched 20/20 job(s).
Fetched 20 unique jobs.
Skipping 0 already processed job(s).
New jobs to analyze: 20

=== Step 2: AI summarization ===
[1/20] Analyzing: DevOps Cloud Engineer (Azure) (d/m/w)
[2/20] Analyzing: Senior Cloud Engineer Kubernetes (w/d/m)
[3/20] Analyzing: Cloud / DevOps Engineer (m/w/d)
[4/20] Analyzing: Senior Cloud Infrastructure Engineer
[5/20] Analyzing: Senior DevOps Engineer (all genders)
[6/20] Analyzing: Senior DevOps Engineer
[7/20] Analyzing: Infrastructure Engineer (f/m/d) Mid-Senior Level - AWS, Kubernetes
[8/20] Analyzing: Staff/Senior Platform Engineer - Cloud Platform (all genders)
[9/20] Analyzing: Senior Cloud Engineer (all genders)
[10/20] Analyzing: Senior DevOps Engineer (m/w/d) aka "Senior Sky Mechanic"
[11/20] Analyzing: Senior Cloud Infrastructure Engineer - Kubernetes (w/m/d)
[12/20] Analyzing: Senior Platform Engineer | Climate Tech startup
[13/20] Analyzing: Senior Cloud Engineer (*)
[14/20] Analyzing: IT-Administrator CloudOps (m/w/d)
[15/20] Analyzing: Senior Cloud Engineer (w/m/d)
[16/20] Analyzing: Senior Cloud Infrastructure Engineer
[17/20] Analyzing: Senior Cloud Infrastructure Engineer (m/w/d)
[18/20] Analyzing: Senior Cloud Infrastructure Engineer | Azure, AWS, GCP | 40 % Home Office, bis 85.000 € (mwd)
[19/20] Analyzing: Senior Infrastructure Engineer (m/f/d)
[20/20] Analyzing: Cloud und Plattform Engineer (w/m/d)

Analyzed 20 job(s), 0 failure(s).

=== Step 3: Generating report ===
Report written to report_2026-08-24.html
Open in browser: open report_2026-08-24.html
```

对应的 HTML 报告示例见 [`examples/report_2026-08-24.html`](examples/report_2026-08-24.html)，用浏览器打开即可预览最终效果：

```bash
# macOS
open examples/report_2026-08-24.html
```

---

## 📊 报告说明

报告为独立 HTML 文件，按国家分组，结构如下：

- **Summary** — 数据库职位总量、高质量匹配数
- **High-Quality Matches** — 评分 ≥ 7、On-site/Hybrid、支持 Relocation 的岗位
- **Other Jobs** — 其余已分析职位

每条职位包含：匹配分、工作类型（On-site/Hybrid/Remote）、技术栈、签证/Relocation 信息、核心职责、原文链接。

无需启动本地服务器，用浏览器直接打开 `report_YYYY-MM-DD.html` 即可阅读。仓库内附带一份示例报告：[`examples/report_2026-08-24.html`](examples/report_2026-08-24.html)。

---

## 📁 项目结构

```
career-job-hunter/
├── config.json          # 搜索条件与个人履历
├── main.py              # 主入口 — 完整流水线
├── job_fetcher.py       # 职位抓取（Apify / RapidAPI + Failover）
├── ai_summarizer.py     # 本地 Ollama AI 分析与打分
├── storage.py           # SQLite 去重与持久化
├── requirements.txt     # 依赖（标准库项目，无第三方包）
├── examples/
│   └── report_2026-08-24.html  # 示例 HTML 报告
├── jobs.db              # 运行时自动生成
├── rapidapi_quota.json  # RapidAPI 额度计数
└── report_*.html        # 每日 HTML 报告
```

---

## 🔧 单独调试各模块

```bash
set -a && source .env && set +a

# 仅测试职位抓取
python job_fetcher.py

# 仅测试 AI 总结
python ai_summarizer.py
```

---

## ❓ 常见问题 (FAQ)

**Q: 只配置了 Apify，没有 RapidAPI，能运行吗？**  
A: 可以。配置 `APIFY_TOKEN` 即可。RapidAPI 是可选 Failover。

**Q: 为什么默认排除 Remote 岗位？**  
A: 示例配置偏向目标国家实地工作（On-site / Hybrid + Visa/Relocation）。这只是默认值。如需包含 Remote，将 `allow_remote` 改为 `true`，并按需调整 `work_types`。

**Q: RapidAPI 25 次/month 够用吗？**  
A: 项目采用单次大吞吐策略（1 次 Request 拉取最多 1000 条），配合本地过滤，20 次/月通常足够日常使用。

**Q: Ollama 模型可以换吗？**  
A: 可以。修改 `ai_summarizer.py` 中的 `DEFAULT_MODEL`，并确保已通过 `ollama pull` 下载对应模型。

---

## 📄 License

MIT — 自由使用与修改，请遵守 LinkedIn、Apify、RapidAPI 各平台的服务条款。
