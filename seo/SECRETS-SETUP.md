# SEO 自动化：账号与 Secrets 配置指南

整套自动化跑在 GitHub Actions 上（每周一早上自动执行），服务器只被动接收同步。
所有凭据都存在 GitHub 仓库的 Secrets 里，不进代码库。

配置入口：仓库页 → `Settings` → `Secrets and variables` → `Actions` → `New repository secret`。

## 当前状态（2026-09-19）

| 模块 | 依赖 Secret | 状态 |
|---|---|---|
| 自动部署（push 即部署 + 健康检查） | `SSH_PRIVATE_KEY` `SSH_HOST` | ✅ 已配置并验证 |
| GSC 每周关键词报表 | `GSC_OAUTH_CLIENT_ID` `GSC_OAUTH_CLIENT_SECRET` `GSC_OAUTH_REFRESH_TOKEN` | ⏳ 前两个已配置，等刷新令牌 |
| IndexNow 推送（Bing/Yahoo/DuckDuckGo） | `INDEXNOW_KEY` | ⏳ 待配置 |
| 每周文章生成 | `LLM_API_KEY`（可选 `LLM_API_BASE` `LLM_MODEL` 变量） | ⏳ 待配置 |

## GSC 报表（OAuth 方案）

Google 对新组织强制禁用了服务账号密钥下载（组织政策
`iam.disableServiceAccountKeyCreation`），因此改用 OAuth 刷新令牌方案：

1. Google Auth Platform（项目 pdf-my99ai）已创建桌面应用客户端 `pdf2md-seo-cli`，
   其 ID/密钥已存为 `GSC_OAUTH_CLIENT_ID` / `GSC_OAUTH_CLIENT_SECRET`
2. 应用已发布为"正式版"（避免测试模式 7 天令牌过期）
3. 刷新令牌获取流程（已于 2026-09-19 完成，无需手工操作）：
   本地捕获授权码 → 换取令牌 → 加密写入 `GSC_OAUTH_REFRESH_TOKEN`

如需重做授权：本地起回环服务接收 `http://localhost:8734` 的授权码，浏览器打开
`https://accounts.google.com/o/oauth2/v2/auth?client_id=<ID>&redirect_uri=http://localhost:8734&response_type=code&scope=https://www.googleapis.com/auth/webmasters.readonly&access_type=offline&prompt=consent`，
再用授权码 + 客户端凭据向 `https://oauth2.googleapis.com/token` 换取刷新令牌，
最后更新 `GSC_OAUTH_REFRESH_TOKEN` Secret。

## IndexNow key

在 [Bing Webmaster Tools](https://www.bing.com/webmasters)（可用 Google 账号直接登录，
支持从 Search Console 一键导入站点）→ IndexNow → 生成 key，存为 `INDEXNOW_KEY`。
部署时会自动写入 `static/<key>.txt` 完成验证；建议同时在 Bing 后台提交 sitemap。

## 每周文章生成

`LLM_API_KEY`：OpenAI / DeepSeek / 智谱 等 API Key。非 OpenAI 时在
Variables 里加 `LLM_API_BASE`（如 `https://api.deepseek.com/v1`）和 `LLM_MODEL`。

流程：每周一 07:00（北京时间）自动取词 → 生成 900-1200 词英文文章 →
质量门槛（词数/内链/禁外链/结构，不通过自动重试一次，仍不通过则放弃发布）→
更新索引/sitemap/RSS → 提交 → 自动部署。

## 工作流说明

- `Deploy`：push 到 google 分支即部署；后端文件变更时自动重建容器；可手动触发
- `SEO weekly`：每周日 23:00 UTC 自动跑（文章 + 周报 + IndexNow）；可手动触发
- `GSC exchange`：一次性，用于换取/更换刷新令牌
