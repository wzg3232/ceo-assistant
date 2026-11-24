"""
CEO助手 - FastAPI主应用 v3 (数据库版)
支持文件分组展示（发票 + 清单）
使用 PostgreSQL 数据库存储 (唯一写方: feishu-approval-ai)
"""
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Depends, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, StreamingResponse
from sqlalchemy.orm import Session
import asyncio
import json
from pathlib import Path
from datetime import datetime
from typing import List, Dict, Any, Optional

from app.database import init_db, get_db
from app.models import Company, Approval
from app.repositories import ApprovalRepository, CompanyRepository
from app.rules import get_rules
from app.rental_checker import detect_rental_settlement
from app.api import webhook
from app.api.skills import router as skills_router

app = FastAPI(title="CEO助手系统", version="3.0.0")
app.include_router(webhook.router)
# 技能仓库 (SkillHub, 2026-11-09)
app.include_router(skills_router)
from app.invoice_export import router as invoice_export_router  # 发票异步导出 (2026-11-19)
app.include_router(invoice_export_router)
app.mount("/static", StaticFiles(directory=str(Path(__file__).parent.parent.parent / "frontend" / "dist")), name="static")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://localhost:5178", "http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

class ConnectionManager:
    def __init__(self):
        self.active_connections: List[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket):
        self.active_connections.remove(websocket)

    async def broadcast(self, message: dict):
        for connection in self.active_connections:
            try:
                await connection.send_json(message)
            except:
                pass

manager = ConnectionManager()


# ---------- 读端口径对齐: 信任写端(feishu-approval-ai)存储的权威值 ----------
# feishu-approval-ai 是唯写方, 已按"真发票口径(仅正式发票) +
#   选项B(差旅费报销扣出差补助)"重跑入 Postgres。读端 main_v3 是纯展示层:
#   - 不再用宽松口径重算并覆盖写端的 total_invoice_amount / list_verify.invoice_total / match;
#   - 写端已存 invoice_total/match 时直接信任; 仅在写端未存(旧数据)时用宽松真发票兜底;
#   - 为差旅费报销补"出差补助"注释, 让详情弹框能解释 审批金额 与 发票总额 的差额。
# 2026-10-14 起: 通行费/过路费形式收据不再视为真发票(不计总额, UI 归「其他单据」)。
TRAVEL_TYPES = ('差旅费报销', '差旅报销')


def _is_real_invoice(inv: dict) -> bool:
    """真发票口径(宽松兜底, 仅写端未存发票总额时使用): 仅 file_type=invoice 的正式发票。"""
    return inv.get('file_type') == 'invoice'


def _is_travel_type(approval_type: str) -> bool:
    """差旅费报销类型判定。DB 真值为 '差旅费报销', 历史值 '差旅报销' 一并兼容。"""
    return (approval_type or '') in TRAVEL_TYPES


def _extract_travel_allowance(raw_data) -> float:
    """从审批表单提取"出差补助费"。真实位置: raw_data['approval_instance']['form']
    是 JSON 字符串, 解析后为 [{name, value}] 列表, 找 出差补助费/差旅补助费/补助 字段。
    找不到/非数值返回 0.0。"""
    if not raw_data:
        return 0.0
    try:
        rd = json.loads(raw_data) if isinstance(raw_data, str) else raw_data
    except Exception:
        return 0.0
    if not isinstance(rd, dict):
        return 0.0
    inst = rd.get('approval_instance') or {}
    form = inst.get('form') if isinstance(inst, dict) else None
    fields = None
    if isinstance(form, str):
        try:
            fields = json.loads(form)
        except Exception:
            fields = None
    elif isinstance(form, list):
        fields = form
    if not isinstance(fields, list):
        return 0.0
    for f in fields:
        if isinstance(f, dict) and f.get('name') in ('出差补助费', '差旅补助费', '补助', 'travel_allowance'):
            v = f.get('value')
            try:
                return float(v)
            except (TypeError, ValueError):
                return 0.0
    return 0.0


def _extract_form_date_interval(raw_data):
    """从表单提取 DateInterval 控件 (start, end, interval), 2026-11-09 路线1 对齐写端。
    interval 为含首尾日历天(飞书 DateInterval.value.interval)。取不到返回 None。"""
    try:
        rd = raw_data if isinstance(raw_data, dict) else (json.loads(raw_data) if raw_data else None)
        if not isinstance(rd, dict):
            return None
        inst = rd.get('approval_instance') or {}
        form = inst.get('form')
        if isinstance(form, str):
            form = json.loads(form)
        if not isinstance(form, list):
            return None
        for f in form:
            if isinstance(f, dict) and f.get('type') == 'dateInterval':
                v = f.get('value') or {}
                return (v.get('start'), v.get('end'), v.get('interval'))
    except Exception:
        return None
    return None


def _extract_form_fields(raw_data, applicant_name: str = ''):
    """从飞书审批表单提取可展示字段, 供详情弹框"基本信息"扩展区 (2026-11-17).
    输出 [{label, value}]: 领款人名单/发放形式/开始时间-结束时间/时长(天)/出差补助费/住宿费/车费/
    其他车费燃油费/其他费用/关联审批/填表人。数值字段 >0 才显示(领导单无出差补助费则自然不出现),
    空值跳过; 金额/出差地点/出差日期 基本信息区已有, 不重复输出。仅读 raw_data, 不改库不重跑。"""
    if not raw_data:
        return []
    try:
        rd = raw_data if isinstance(raw_data, dict) else (json.loads(raw_data) if raw_data else None)
    except Exception:
        return []
    if not isinstance(rd, dict):
        return []
    inst = rd.get('approval_instance') or {}
    form = inst.get('form')
    if isinstance(form, str):
        try:
            form = json.loads(form)
        except Exception:
            return []
    if not isinstance(form, list):
        return []
    out = []
    for f in form:
        if not isinstance(f, dict):
            continue
        name, ty, val = f.get('name'), f.get('type'), f.get('value')
        if ty == 'contact' and name in ('填表人', '提交人'):
            if applicant_name:
                out.append({'label': '填表人', 'value': applicant_name})
            continue
        if ty == 'textarea' and val not in (None, ''):
            out.append({'label': name, 'value': str(val)})
            continue
        if ty == 'radioV2':
            opt = f.get('option') or {}
            text = opt.get('text') if isinstance(opt, dict) else None
            if text:
                out.append({'label': name, 'value': text})
            continue
        if ty == 'dateInterval' and isinstance(val, dict):
            start = str(val.get('start') or '')[:10]
            end = str(val.get('end') or '')[:10]
            if start:
                out.append({'label': '开始时间-结束时间', 'value': f'{start} ~ {end}' if end else start})
            if val.get('interval'):
                out.append({'label': '时长(天)', 'value': f'{val["interval"]}天'})
            continue
        if ty == 'number':
            try:
                n = float(val)
            except (TypeError, ValueError):
                continue
            n = int(n) if n == int(n) else n
            if n > 0:
                out.append({'label': name, 'value': n})
            continue
        if ty == 'connect':
            ext = f.get('ext') or {}
            sids = ext.get('serialIDs') if isinstance(ext, dict) else None
            if sids:
                out.append({'label': '关联审批', 'value': str(sids)})
            continue
        # amount(基本信息"审批金额") / input出差地点("出差地点") / text说明 / 空值 → 跳过
    return out


def _extract_form_attachments(raw_data, serial_number: str):
    """提取表单 attachmentV2 的附件原件 (2026-11-17): ext=原始文件名逗号串, value=下载URL串, 按下标配对。
    图片原件(jpg等)也在其中。下载统一走 files/{serial}/{文件名} (缓存命中直出, miss 时后端飞书回源);
    URL 是【相对路径】(无开头/), 浏览器按当前页面 base 解析: 根部署 http://host:8002/ → /files/...,
    nginx 网关子路径 http://host:1111/ceo/ → /ceo/files/... (网关catchall剥前缀转8002, 2026-11-17修复
    "B台网关下下载报无法提取文件": 原绝对路径 /files/ 被网关 location /files 转给 WeKnora 401)。"""
    out = []
    if not raw_data:
        return out
    try:
        rd = raw_data if isinstance(raw_data, dict) else (json.loads(raw_data) if raw_data else None)
    except Exception:
        return out
    if not isinstance(rd, dict):
        return out
    inst = rd.get('approval_instance') or {}
    form = inst.get('form')
    if isinstance(form, str):
        try:
            form = json.loads(form)
        except Exception:
            return out
    if not isinstance(form, list):
        return out
    base_dir = Path(__file__).parent.parent / "files"
    from urllib.parse import quote
    for f in form:
        if not isinstance(f, dict) or f.get('type') != 'attachmentV2':
            continue
        ext = f.get('ext')
        names = [x.strip() for x in str(ext or '').split(',') if x.strip()]
        for fname in names:
            cached = (base_dir / f"{serial_number}__{fname}").is_file()
            out.append({'filename': fname, 'url': f"files/{serial_number}/{quote(fname, safe='')}", 'cached': cached})
    return out


def _build_travel_allowance_check(list_items, raw_data, approval_type) -> dict:
    """读端重算 出差补助核对。2026-11-09 对齐写端路线1口径:
    基准 = 表单 DateInterval.interval(含首尾日历天) × 80, 与表单补助金额核对;
    清单天数(明细行数)只作"多填"护栏(>声明天数才 warn), 少填不算错(附提示)。
    表单无 DateInterval 时退回旧口径(清单日期行数)。
    仅 差旅费报销 类型且有 出差补助费 字段时返回; 字段:
    travel_allowance_check / travel_dates / expected_allowance / travel_allowance。"""
    if not _is_travel_type(approval_type):
        return {}
    allowance = _extract_travel_allowance(raw_data)
    if allowance <= 0:
        return {}
    list_dates = sorted({li['date'][:10] for li in list_items if li.get('date')})

    di = _extract_form_date_interval(raw_data)
    base_days = None
    travel_dates = list_dates
    if di and di[2] is not None:
        try:
            base_days = int(di[2])  # 含首尾日历天, 与写端 check_allowance_days 硬核对基准一致
        except (TypeError, ValueError):
            base_days = None
        if base_days:
            # 展开日历日期(上限 31 天防异常超大区间); start 缺省时只保留声明天数
            if di[0]:
                from datetime import datetime, timedelta
                try:
                    d0 = datetime.strptime(str(di[0])[:10], '%Y-%m-%d').date()
                    travel_dates = [str(d0 + timedelta(days=i))[:10] for i in range(min(base_days, 31))]
                except Exception:
                    travel_dates = list_dates
            else:
                travel_dates = list_dates

    expected = base_days * 80 if base_days else len(list_dates) * 80
    passed = abs(allowance - expected) < 1
    hint = ''
    if base_days and len(list_dates) < base_days:
        hint = f' (清单仅{len(list_dates)}行, 无费用行程未填, 以表单声明{base_days}天为准)'
    elif base_days and len(list_dates) > base_days:
        hint = f' (清单{len(list_dates)}行 > 声明{base_days}天, 请人工核对)'
    return {
        'travel_allowance_check': {
            'passed': passed,
            'message': f'差旅补助: {allowance}元 (期望{expected}元, {base_days if base_days else len(list_dates)}天×80元/天)' + hint,
        },
        'travel_dates': travel_dates,
        'expected_allowance': expected,
        'travel_allowance': round(allowance, 2),
    }


def _apply_invoice_total_rule(item: dict, approval=None, raw_data=None):
    """读端口径对齐: 信任写端权威值, 不覆盖写端结论, 仅补补助注释。"""
    lv = item.get('list_verify')

    # 1) 发票总额: 写端已存则信任(DB), 仅未存(旧数据)时宽松兜底
    if lv is not None and lv.get('invoice_total') is None:
        real = [inv for inv in item.get('invoices', []) if _is_real_invoice(inv)]
        real_total = round(sum(float(inv.get('total_amount') or 0) for inv in real), 2)
        lv['invoice_total'] = real_total
        lv['invoice_count'] = len(real)
        if item.get('total_invoice_amount') in (None, 0):
            item['total_invoice_amount'] = real_total
    elif lv is None and item.get('total_invoice_amount') in (None, 0) and item.get('invoices'):
        real = [inv for inv in item['invoices'] if _is_real_invoice(inv)]
        item['total_invoice_amount'] = round(sum(float(inv.get('total_amount') or 0) for inv in real), 2)

    # 2) match: 信任写端存储的 is_match(差旅费报销已按选项B扣补助), 仅未存时朴素比对
    if lv is not None and lv.get('match') is None:
        if lv.get('list_total') is not None and lv.get('invoice_total') is not None:
            lv['match'] = abs(float(lv['list_total']) - float(lv['invoice_total'])) < 0.01

    # 3) 差旅费报销: 补"出差补助"注释(展示用, 不改变 match), 解释 审批 vs 发票 差额
    if approval is not None and _is_travel_type(getattr(approval, 'approval_type', '')):
        allowance = _extract_travel_allowance(raw_data)
        if allowance > 0 and lv is not None:
            lv['travel_allowance'] = round(allowance, 2)
            lv['comparable_amount'] = round(float(item.get('approval_amount') or 0) - allowance, 2)

    # 4) 写端判定三方一致(match=True)时, 移除写端遗留的过期"审批金额核对"issue
    if lv is not None and lv.get('match') and item.get('issues'):
        item['issues'] = [i for i in item['issues'] if i.get('type') != '审批金额核对']


@app.on_event("startup")
async def startup_event():
    """启动时初始化数据库"""
    init_db()


@app.get("/api/rules")
async def get_rules_endpoint(approval_type: Optional[str] = None):
    """获取审核规则"""
    if approval_type:
        return {"rules": get_rules(approval_type)}
    return {"expense_rules": get_rules("费用报销"), "travel_rules": get_rules("差旅报销")}


# ===== 发票七分类 (含 车辆通行费) + 非发票剔除 (app.invoice_classify 单一来源, 列表/导出共用) =====
from app.invoice_classify import classify_invoice as _classify_invoice  # noqa: E402
from app.invoice_classify import is_not_invoice as _is_not_invoice  # noqa: E402

@app.get("/api/approvals")
async def get_approvals(db: Session = Depends(get_db)):
    """获取审批列表"""
    repo = ApprovalRepository(db)
    approvals = repo.get_all()
    
    result = []
    for approval in approvals:
        item = approval.to_dict()
        # 获取关联数据
        _inv_objs = list(repo.get_invoices(approval.id))
        item['invoices'] = []
        for _inv in _inv_objs:
            _invd = _inv.to_dict()
            if _is_not_invoice(_invd, getattr(_inv, 'raw_json', None)):
                continue  # 非发票不下发 (DB保留)
            _invd['invoice_class'] = _classify_invoice(_invd, getattr(_inv, 'raw_json', None))
            item['invoices'].append(_invd)
        item['lists'] = [l.to_dict() for l in repo.get_lists(approval.id)]
        item['trip_summaries'] = [t.to_dict() for t in repo.get_trips(approval.id)]
        item['issues'] = [i.to_dict() for i in repo.get_issues(approval.id)]
        
        # 查找list_verify并补充清单明细与补助核对
        for lst in item['lists']:
            if lst.get('match') is not None or lst.get('list_total') is not None:
                lst_copy = dict(lst)
                list_items = [li.to_dict() for li in repo.get_list_items(lst['id'])]
                lst_copy['list_items'] = list_items
                # 恢复补助核对（DB 真值 '差旅费报销'; 旧代码用 '差旅报销' 枚举从未命中）
                allow_fields = _build_travel_allowance_check(list_items, approval.raw_data, approval.approval_type)
                if allow_fields:
                    lst_copy.update(allow_fields)
                item['list_verify'] = lst_copy
                break

        # 跨单重复发票号标记（豁免不判 fail，但列表显眼标记）
        # 逻辑：该单的所有发票号是否出现在其他 approval_id 里
        invoice_numbers = [inv['invoice_number'] for inv in item['invoices'] 
                          if inv.get('invoice_number') and inv.get('file_type') == 'invoice']
        duplicate_serials = []
        if invoice_numbers:
            # 查库：该单发票号被哪些其他单持有
            from sqlalchemy import text
            sql = text("""
                SELECT DISTINCT a2.serial_number
                FROM invoices i2 
                JOIN approvals a2 ON a2.id = i2.approval_id 
                WHERE i2.invoice_number IN :nums 
                AND a2.serial_number != :serial
                AND i2.file_type = 'invoice'
                ORDER BY a2.serial_number
            """)
            dup_result = db.execute(sql, {"nums": tuple(invoice_numbers), "serial": approval.serial_number})
            duplicate_serials = [row[0] for row in dup_result.fetchall()]
        item['has_duplicate_invoice'] = ','.join(duplicate_serials) if duplicate_serials else None
        # 发票号平铺 (前端搜索框按发票号命中该单; 仅正式发票, 去重保序)
        item['invoice_numbers'] = []
        for inv in item['invoices']:
            n = inv.get('invoice_number')
            if inv.get('file_type') == 'invoice' and n and n not in item['invoice_numbers']:
                item['invoice_numbers'].append(n)

        # 添加审核规则
        item['rules'] = get_rules(approval.approval_type or '费用报销')

        # 租车结算单审核（仅差旅费报销类型）
        if _is_travel_type(approval.approval_type):
            rental_check = detect_rental_settlement(item['invoices'], item['trip_summaries'], list_items=(item.get('list_verify') or {}).get('list_items') or [])
            item['rental_check'] = rental_check

        # 读端口径对齐: 信任写端权威值, 不覆盖; 补补助注释; 兜底旧数据
        _apply_invoice_total_rule(item, approval, approval.raw_data)

        result.append(item)
    
    return {
        'total': len(result),
        'generated_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'groups': {a['serial_number']: a for a in result}
    }


@app.get("/api/approvals/{serial_number}")
async def get_approval(serial_number: str, db: Session = Depends(get_db)):
    """获取单个审批单详情"""
    repo = ApprovalRepository(db)
    approval = repo.get_by_serial(serial_number)

    if not approval:
        raise HTTPException(status_code=404, detail="审批单不存在")

    item = approval.to_dict()
    item['invoices'] = [i.to_dict() for i in repo.get_invoices(approval.id)]
    item['lists'] = [l.to_dict() for l in repo.get_lists(approval.id)]
    item['trip_summaries'] = [t.to_dict() for t in repo.get_trips(approval.id)]
    item['issues'] = [i.to_dict() for i in repo.get_issues(approval.id)]

    # 查找list_verify并补充清单明细与补助核对
    for lst in item['lists']:
        if lst.get('match') is not None or lst.get('list_total') is not None:
            lst_copy = dict(lst)
            list_items = [li.to_dict() for li in repo.get_list_items(lst['id'])]
            lst_copy['list_items'] = list_items
            # 恢复补助核对（DB 真值 '差旅费报销'; 旧代码用 '差旅报销' 枚举从未命中）
            allow_fields = _build_travel_allowance_check(list_items, approval.raw_data, approval.approval_type)
            if allow_fields:
                lst_copy.update(allow_fields)
            item['list_verify'] = lst_copy
            break

    # 跨单重复发票号标记（豁免不判 fail，但列表显眼标记）
    invoice_numbers = [inv['invoice_number'] for inv in item['invoices'] 
                      if inv.get('invoice_number') and inv.get('file_type') == 'invoice']
    duplicate_serials = []
    if invoice_numbers:
        from sqlalchemy import text
        sql = text("""
            SELECT DISTINCT a2.serial_number
            FROM invoices i2 
            JOIN approvals a2 ON a2.id = i2.approval_id 
            WHERE i2.invoice_number IN :nums 
            AND a2.serial_number != :serial
            AND i2.file_type = 'invoice'
            ORDER BY a2.serial_number
        """)
        dup_result = db.execute(sql, {"nums": tuple(invoice_numbers), "serial": approval.serial_number})
        duplicate_serials = [row[0] for row in dup_result.fetchall()]
    item['has_duplicate_invoice'] = ','.join(duplicate_serials) if duplicate_serials else None

    # 添加审核规则
    item['rules'] = get_rules(approval.approval_type or '费用报销')

    # 租车结算单审核（仅差旅费报销类型）
    if _is_travel_type(approval.approval_type):
        rental_check = detect_rental_settlement(item['invoices'], item['trip_summaries'], list_items=(item.get('list_verify') or {}).get('list_items') or [])
        item['rental_check'] = rental_check

    # 读端口径对齐: 信任写端权威值, 不覆盖; 补补助注释; 兜底旧数据
    _apply_invoice_total_rule(item, approval, approval.raw_data)

    # 类目逐笔对账 (2026-11-03): 差旅费报销才做; 读端独立计算, 不改库、历史单立即可见。
    # 输出每类的 清单/发票/表单 总额 + matched/仅在清单/仅在发票, 供前端点类目差异看明细。
    if _is_travel_type(approval.approval_type):
        try:
            from app.category_reconcile import build_category_reconcile
            _items = (item.get('list_verify') or {}).get('list_items') or []
            # 发票需带 raw_json 才能按明细名归类(住宿/客运服务费/通行费…),
            # 而 Invoice.to_dict() 不含 raw_json → 这里直接用 ORM 对象补齐, 仅供对账计算。
            _invs = []
            for _o in repo.get_invoices(approval.id):
                _d = _o.to_dict()
                _d['raw_json'] = getattr(_o, 'raw_json', None)
                _invs.append(_d)
            item['category_reconcile'] = build_category_reconcile(
                _invs, _items, approval.raw_data)
        except Exception as e:
            logger.error(f"类目逐笔对账计算失败 {serial_number}: {e}")
            item['category_reconcile'] = None

    # 表单字段 + 附件原件 (2026-11-17): 详情弹框"基本信息"扩展区; 全类型通用, 缺字段自然为空。
    item['form_fields'] = _extract_form_fields(approval.raw_data, approval.applicant_name)
    item['form_attachments'] = _extract_form_attachments(approval.raw_data, approval.serial_number)

    return item


@app.get("/api/statistics")
async def get_statistics(db: Session = Depends(get_db)):
    """获取统计数据"""
    repo = ApprovalRepository(db)
    return repo.get_statistics()


@app.get("/api/companies")
async def get_companies(db: Session = Depends(get_db)):
    """获取公司列表"""
    repo = CompanyRepository(db)
    companies = repo.get_all()
    return {"companies": [c.to_dict() for c in companies]}


@app.get("/api/search")
async def search_approvals(keyword: str, status: Optional[str] = None, db: Session = Depends(get_db)):
    """搜索审批单"""
    repo = ApprovalRepository(db)
    approvals = repo.search(keyword, status)
    return {
        'total': len(approvals),
        'approvals': [a.to_dict() for a in approvals]
    }


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await manager.connect(websocket)
    try:
        while True:
            data = await websocket.receive_text()
            if data == "ping":
                await websocket.send_text("pong")
    except WebSocketDisconnect:
        manager.disconnect(websocket)


# ---------------- 事件驱动刷新通道 (SSE) ----------------
# 写端 feishu-approval-ai(5003) 入 Postgres 成功后调用 /api/events/ping,
# 这里向所有已连接的前端 SSE 客户端广播, 前端收到即重新拉取审批列表。
import time

_event_subscribers: set = set()


async def _sse_stream():
    q: asyncio.Queue = asyncio.Queue()
    _event_subscribers.add(q)
    try:
        yield ": connected\n\n"
        while True:
            try:
                msg = await asyncio.wait_for(q.get(), timeout=25)
                yield f"data: {json.dumps(msg, ensure_ascii=False)}\n\n"
            except asyncio.TimeoutError:
                # 保活, 防止中间代理断开长连接
                yield ": keepalive\n\n"
    except asyncio.CancelledError:
        pass
    finally:
        _event_subscribers.discard(q)


@app.get("/api/events/stream")
async def events_stream():
    """SSE 订阅端: 前端 EventSource 连接, 收到 refresh 事件即刷新列表"""
    return StreamingResponse(
        _sse_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.post("/api/events/ping")
async def events_ping(payload: Optional[Dict[str, Any]] = None):
    """触发端: 5003 写库成功后调用, 推送给所有已连接前端"""
    msg = {"ts": time.time(), "source": "audit-write", **(payload or {})}
    for q in list(_event_subscribers):
        q.put_nowait(msg)
    return {"code": 0, "msg": "pushed", "subscribers": len(_event_subscribers)}


@app.get("/files/{serial}/{filename}")
async def download_approval_file(serial: str, filename: str):
    """附件文件下载 (穿透公网可直接访问): 文件由飞书审批附件缓存落本地.
    路径规则: files/<serial>/<原始文件名> -> backend/files/<serial>__<原始文件名>"""
    from urllib.parse import unquote
    base_dir = (Path(__file__).parent.parent / "files").resolve()
    raw_name = unquote(filename)
    # 路径穿越加固: 解码后含分隔符/点号相对路径段的一律拒绝 (正常文件名不含 / \ ..)
    if any(ch in raw_name for ch in ("/", "\\")) or ".." in raw_name.split("."):
        raise HTTPException(status_code=400, detail="非法文件名")
    serial_safe = unquote(serial).replace("\\", "_").replace("/", "_")
    if ".." in serial_safe:
        raise HTTPException(status_code=400, detail="非法 serial")
    file_path = (base_dir / f"{serial_safe}__{raw_name}").resolve()
    if file_path.is_file() and str(file_path).startswith(str(base_dir)):
        media = "application/pdf" if file_path.suffix.lower() == ".pdf" else None
        return FileResponse(file_path, media_type=media, filename=raw_name)

    # 缓存 miss → 飞书回源兜底 (2026-11-17): 取该单 raw_data 里 attachmentV2 的下载URL,
    # 实时拉原件写回缓存再下发。飞书 authcode 短链 ~24h 过期, 回源每次拿的都是库里存的
    # 原URL, 若已过期/失效则 404 提示, 不静默吞。
    try:
        import requests as _rq
        from app.database import SessionLocal
        from app.models import Approval as _Approval
        _db = SessionLocal()
        try:
            _ap = _db.query(_Approval).filter(_Approval.serial_number == unquote(serial)).first()
        finally:
            _db.close()
        if not _ap or not _ap.raw_data:
            raise HTTPException(status_code=404, detail=f"文件不存在且无回源数据: {serial}/{filename}")
        # 回源顺序: ① 飞书 live API 实时取该实例最新 attachmentV2 URL(authcode 实时生成, 老单也能拉)
        # ② 兜底用 raw_data 里存的旧 URL(可能已过期)。匹配文件名: ext 名串 与 value URL串 按下标配对。
        _dl_url = None
        _live = None
        try:
            from app.api.webhook import get_approval_status
            _live = get_approval_status(_ap.approval_id)
        except Exception:
            _live = None
        if _live:
            _lform = _live.get('form')
            if isinstance(_lform, str):
                try:
                    _lform = json.loads(_lform)
                except Exception:
                    _lform = None
            for _f in (_lform or []):
                if not isinstance(_f, dict) or _f.get('type') != 'attachmentV2':
                    continue
                _lnames = [x.strip() for x in str(_f.get('ext') or '').split(',')]
                _lurls = _f.get('value') or []
                if isinstance(_lurls, str):
                    _lurls = [_lurls]
                for _i, _nm in enumerate(_lnames):
                    if _nm == raw_name and _i < len(_lurls):
                        _dl_url = _lurls[_i]
                        break
                if _dl_url:
                    break
        if not _dl_url:
            # 兜底: raw_data 旧 URL
            _rd = _ap.raw_data if isinstance(_ap.raw_data, dict) else json.loads(_ap.raw_data)
            _inst = _rd.get('approval_instance') or {}
            _form = _inst.get('form')
            if isinstance(_form, str):
                _form = json.loads(_form)
            for _f in (_form or []):
                if not isinstance(_f, dict) or _f.get('type') != 'attachmentV2':
                    continue
                _names = [x.strip() for x in str(_f.get('ext') or '').split(',')]
                _urls = _f.get('value') or []
                if isinstance(_urls, str):
                    _urls = [_urls]
                for _i, _nm in enumerate(_names):
                    if _nm == raw_name and _i < len(_urls):
                        _dl_url = _urls[_i]
                        break
                if _dl_url:
                    break
        if not _dl_url:
            raise HTTPException(status_code=404, detail=f"文件不存在: {serial}/{filename}")
        _hdrs = {}
        try:
            from app.api.webhook import get_feishu_token
            _tk = get_feishu_token()
            if _tk:
                _hdrs['Authorization'] = f'Bearer {_tk}'
        except Exception:
            pass
        _resp = _rq.get(_dl_url, headers=_hdrs, timeout=30)
        if _resp.status_code != 200 or not _resp.content:
            raise HTTPException(status_code=404, detail=f"飞书回源失败(HTTP {_resp.status_code}): {filename}")
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_bytes(_resp.content)
        media = "application/pdf" if file_path.suffix.lower() == ".pdf" else None
        return FileResponse(file_path, media_type=media, filename=raw_name,
                            headers={"X-Feishu-Fallback": "1"})
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"飞书回源异常: {e}")


@app.get("/files")
async def list_approval_files():
    """列出所有已缓存的审批附件文件 (按 serial 分组)"""
    base_dir = Path(__file__).parent.parent / "files"
    result = {}
    if base_dir.exists():
        from urllib.parse import quote
        for f in sorted(base_dir.iterdir()):
            if f.name.endswith("__") or "__" not in f.name:
                continue
            serial, _, fname = f.name.partition("__")
            result.setdefault(serial, []).append({
                "filename": fname,
                "url": f"/files/{serial}/{quote(fname, safe='')}",
                "size": f.stat().st_size,
            })
    return {"serials": result}


@app.get("/{full_path:path}")
async def serve_frontend(full_path: str):
    """Serve the React frontend SPA for any non-API path."""
    from pathlib import Path
    dist_dir = Path(__file__).parent.parent.parent / "frontend" / "dist"
    # Try to serve as static file first (for /assets/ and /static/ paths)
    if full_path.startswith("static/") or full_path.startswith("assets/"):
        file_path = dist_dir / full_path
        if file_path.exists():
            return FileResponse(file_path)
    # Otherwise serve index.html for SPA routing (禁缓存: 保证普通刷新拿到最新 JS/CSS 引用)
    index_path = dist_dir / "index.html"
    if index_path.exists():
        return FileResponse(
            index_path,
            headers={
                "Cache-Control": "no-cache, no-store, must-revalidate",
                "Pragma": "no-cache",
                "Expires": "0",
            },
        )
    raise HTTPException(status_code=404, detail="Frontend not found")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8002)

