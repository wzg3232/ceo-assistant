# CEO助手系统

企业CEO智能分身系统 - 财务审批审核模块。

## 技术栈

- **后端**: FastAPI + Python（PostgreSQL 审计库）
- **前端**: React 18 + TypeScript + Vite
- **实时通信**: WebSocket
- **数据库**: PostgreSQL `ceo_audit`（由 feishu-approval-ai 服务作为唯一写方维护 schema，本服务只读）
- **飞书集成**: 审批状态 Webhook、附件原件回源下载

## 项目结构

```
ceo-assistant/
├── backend/              # FastAPI后端
│   ├── app/
│   │   ├── main_v3.py    # 主应用入口 (uvicorn app.main_v3:app)
│   │   ├── main.py       # v2 旧版主应用 (含飞书附件拉取逻辑)
│   │   ├── database.py   # Postgres 连接配置 (读环境变量)
│   │   ├── invoice_export.py  # 发票批量导出 (7类合并PDF+Excel汇总)
│   │   ├── invoice_classify.py# 发票七分类单一来源
│   │   ├── models/       # SQLAlchemy 模型 (approvals/invoices/export_history...)
│   │   └── api/          # 路由 (webhook/skills/invoices)
│   ├── pdf_invoice_parser.py
│   └── requirements.txt
├── frontend/            # React前端
│   ├── src/
│   │   ├── App.tsx      # 主组件 (审核看板/发票列表/导出弹框)
│   │   ├── api.ts       # API调用
│   │   └── types.ts     # TypeScript类型
│   ├── package.json
│   └── vite.config.ts
├── start.sh             # 生产启动脚本 (确保Postgres容器存活 + 拉起uvicorn 8002)
├── .env.example         # 环境变量模板
└── DATABASE_GUIDE.md    # 数据库字段说明
```

## 快速开始

### 1. 配置环境变量

```bash
cp .env.example .env
# 编辑 .env, 填入飞书 app_id / app_secret 和 Postgres 连接串
```

需要三个环境变量（详见 `.env.example`）：
- `FEISHU_APP_ID` / `FEISHU_APP_SECRET` — 飞书应用凭据
- `CEO_AUDIT_DB_URL` — Postgres 审计库连接串

### 2. 启动后端

```bash
cd backend
pip install -r requirements.txt
export $(grep -v '^#' ../.env | xargs)   # 或 source ../.env
python -m uvicorn app.main_v3:app --host 0.0.0.0 --port 8002
```

后端运行在: http://localhost:8002 （8002 同时由 uvicorn 托管 frontend/dist 静态文件）

### 3. 启动前端（开发模式）

```bash
cd frontend
npm install
npm run dev        # http://localhost:5173
```

构建静态文件：`npm run build`（产物在 `frontend/dist`，已被 .gitignore 排除）。

## 功能特性

- ✅ 按部门子菜单的审批看板（财务部/采购部/行政部）
- ✅ 发票列表：七分类、导出状态列（含全部导出历史）、批量勾选导出
- ✅ 导出二次确认弹框 + 环形进度条，批量导出打包为 zip（7类合并PDF + 1份Excel汇总）
- ✅ 单张发票多格式下载（PDF/OFD/XML），下载计入导出历史
- ✅ 搜索/筛选（发票类型、日期、发票号），筛选变更自动清空已勾选
- ✅ WebSocket 实时推送

## 分支约定

本项目采用两条分支，约定如下：

| 分支 | 用途 | 说明 |
|------|------|------|
| `dev` | 日常开发 | 所有新功能、改 bug、加字段都先提交到 `dev`，在 `dev` 上验证通过 |
| `main` | 稳定可跑版本 | 只接收来自 `dev` 的合并，克隆下来即可直接部署生产（如 8002 面板） |

### 合并流程

1. 日常开发一律在 `dev` 上进行：

   ```bash
   git checkout dev
   # ... 改代码、本地验证 ...
   git add -A && git commit -m "feat: xxx"
   git push origin dev
   ```

2. `dev` 验证 OK 后合并到 `main`：

   ```bash
   git checkout main
   git merge dev        # 内容一致时会是 fast-forward
   git push origin main
   ```

3. 合并 `main` 后如需更新生产部署，按「快速开始」重新构建并同步：

   ```bash
   cd frontend && npm run build
   # 同步 frontend/dist 到生产目录并重启 8002 (见 start.sh)
   ```

### 规则

- **不允许**直接在 `main` 上提交未经 `dev` 验证的代码
- `main` 随时保持可部署状态；出了问题就回退到上一个好的 commit
- 生产环境（如 8002）只运行 `main` 分支的代码

## 安全说明

- 飞书应用凭据与数据库密码**一律不硬编码**，只从环境变量读取；`.env` 已被 git 忽略
- `backend/files/`（发票原件）、`backups/`（SQL备份）、`audit_data.db`（本地库）均不入库
