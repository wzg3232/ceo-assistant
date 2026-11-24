# CEO助手 - SQLite数据库连接指南

## 数据库位置
```
D:\program\ceo-assistant\backend\audit_data.db
```

## DBeaver连接步骤

### 1. 安装SQLite驱动
- DBeaver 默认已内置 SQLite 驱动
- 如果没有，点击 `驱动管理器` → `添加` → 选择 `SQLite`

### 2. 创建新连接
1. 打开 DBeaver
2. 点击 `新建连接` (或 Ctrl+N)
3. 在搜索框输入 `SQLite`
4. 选择 `SQLite` 驱动类型
5. 点击 `下一步`

### 3. 配置数据库文件
1. **数据库文件**: 点击 `...` 浏览选择
   ```
   D:\program\ceo-assistant\backend\audit_data.db
   ```
2. **连接测试**: 点击 `测试连接` 确认成功
3. 点击 `完成`

### 4. 浏览数据库
连接成功后，左侧导航栏会显示：
```
└── audit_data.db
    ├── companies (1条)
    ├── approvals (15条)
    ├── invoices (130条)
    ├── approval_lists (9条)
    ├── list_items (0条)
    ├── trip_summaries (7条)
    ├── issues (9条)
    └── audit_logs (0条)
```

## 常用查询示例

### 查看所有审批单
```sql
SELECT serial_number, applicant_name, approval_type, approval_amount, status
FROM approvals
ORDER BY start_time DESC;
```

### 查看某审批单的发票
```sql
SELECT i.file_name, i.invoice_type, i.total_amount, i.is_compliant
FROM invoices i
WHERE i.approval_id = 'A2974A7A-C494-4A55-9DA2-2BF5574E2D11';
```

### 查看行程汇总
```sql
SELECT t.file_name, t.trip_type, t.trips, t.total_amount, t.start_date
FROM trip_summaries t
WHERE t.approval_id = 'A2974A7A-C494-4A55-9DA2-2BF5574E2D11';
```

### 查看公司信息
```sql
SELECT name, app_id, app_id_short FROM companies;
```

## 数据库特点

- **文件式数据库**: 单个 .db 文件，可直接复制备份
- **零配置**: 无需安装数据库服务
- **跨平台**: Windows/Mac/Linux 通用
- **FastAPI原生支持**: SQLAlchemy 直接连接
