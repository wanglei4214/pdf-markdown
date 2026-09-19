# SEO 自动化：账号与 Secrets 配置指南

整套自动化跑在 GitHub Actions 上（每周一早上自动执行），服务器只被动接收同步。
本文列出每个凭据的用途、获取方法和配置位置。**所有凭据都只存在 GitHub 仓库的
Secrets 里，不进代码库。**

配置入口：GitHub 仓库页 → `Settings` → `Secrets and variables` → `Actions` →
`New repository secret`（变量类用 `Variables` 标签页）。

---

## 一、部署链路（必需，配置后整站自动部署生效）

| 名称 | 位置 | 值 |
|---|---|---|
| `SSH_PRIVATE_KEY` | Secret | 本地文件 `seo/deploy_key_ed25519` 的**完整内容**（含 `-----BEGIN...` 到 `-----END...` 全部行） |
| `SSH_HOST` | Secret | `47.238.7.0` |

- 公钥已装到服务器 `root@47.238.7.0` 的 `authorized_keys`（注释 pdf2md-deploy@github-actions）。
- 私钥文件已在 `.gitignore` 中排除（`seo/deploy_key*`），**绝不提交**。
- 配置好后：推送到 `google` 分支即自动同步服务器并做健康检查；后端文件变更时自动
  `docker compose up -d --build`，纯静态变更秒级生效。
- 想手动部署：Actions → `Deploy` → `Run workflow`（可勾选"强制重建后端"）。

## 二、每周文章生成（推荐）

| 名称 | 位置 | 值 |
|---|---|---|
| `LLM_API_KEY` | Secret | OpenAI / DeepSeek / 智谱 等 API Key |
| `LLM_API_BASE` | Variable | 可选。非 OpenAI 时填对应地址，如 DeepSeek: `https://api.deepseek.com/v1` |
| `LLM_MODEL` | Variable | 可选。如 `deepseek-chat`、`glm-4-flash`，默认 `gpt-4o-mini` |

流程：每周一 07:00（北京时间）自动从 `seo/keywords.json` 取下一个未用关键词 →
LLM 写 900-1200 词英文文章（带质量门槛：词数、内链数、禁外链、结构检查）→
更新博客索引/sitemap/RSS → 提交入库 → 自动部署 → 通知搜索引擎。
质量门槛不通过会重试一次，仍不通过则放弃本次，**不会发布低质内容**。

词库用完后 Actions 会显示提示，往 `seo/keywords.json` 的 `keywords` 数组补充新词即可。

## 三、Google Search Console 周报（推荐，需 Google 账号一次性操作）

| 名称 | 位置 | 值 |
|---|---|---|
| `GSC_SERVICE_ACCOUNT_JSON` | Secret | 服务账号 JSON 密钥全文 |

说明：无人值守程序不能用你的 Google 通行密钥（passkey）登录，Google 官方方式是
**服务账号（service account）**，一次配置长期有效：

1. 用 huoguo0913@gmail.com 登录 [console.cloud.google.com](https://console.cloud.google.com)
2. 顶部选/新建项目（名字随意，如 `pdf2md-seo`）
3. 「API 和服务 → 库」搜索 **Search Console API** → 启用
4. 「凭据 → 创建凭据 → 服务账号」：名字随意，一路创建完成
5. 进入该服务账号 → 「密钥」→「添加密钥 → JSON」→ 下载 JSON 文件
6. 打开 [Search Console](https://search.google.com/search-console)（选 pdf.my99ai.com 属性）
   → 设置 → 用户和权限 → 添加用户：
   填服务账号的邮箱（形如 `xxx@pdf2md-seo.iam.gserviceaccount.com`），权限"完整"
7. 把第 5 步 JSON 文件的**全文**粘贴为 `GSC_SERVICE_ACCOUNT_JSON`

配置后每周自动生成过去 7 天的查询词报表（点击/展现/CTR/排名），写入
`seo/reports/` 并显示在 Actions 运行页面的 Summary 里。

## 四、Bing / IndexNow 主动推送（推荐，需 Microsoft 账号）

| 名称 | 位置 | 值 |
|---|---|---|
| `INDEXNOW_KEY` | Secret | Bing Webmaster Tools → IndexNow → 生成的 key |

说明：
- 覆盖 Bing / Yahoo / DuckDuckGo / Yandex 等。**Google 不支持 IndexNow**，
  Google 侧靠 sitemap 的 lastmod 更新自然重抓（已在部署流程中自动处理）。
- 部署时会把 `static/<key>.txt` 写进站点做所有权验证，无需手工操作。
- [Bing Webmaster Tools](https://www.bing.com/webmasters) 可直接用 Google 账号登录，
  并支持"从 Google Search Console 导入"站点，一分钟完成初始化。建议顺手在里面
  提交一次 sitemap：`https://pdf.my99ai.com/sitemap.xml`。

## 五、当前状态速查

| 模块 | 依赖 | 状态 |
|---|---|---|
| 自动部署（push 即部署 + 健康检查） | SSH_PRIVATE_KEY, SSH_HOST | ⏳ 等你把上面两个值贴进 Secrets |
| 每周文章 | LLM_API_KEY | ⏳ 等 API Key |
| GSC 周报 | GSC_SERVICE_ACCOUNT_JSON | ⏳ 等服务账号 JSON |
| IndexNow 推送 | INDEXNOW_KEY | ⏳ 等 Bing key |
| sitemap/RSS/OG图 自动维护 | 无 | ✅ 已生效 |

任何一项没配置时对应任务会自动跳过并在 Actions 日志里给黄色提示，不影响其他任务。
