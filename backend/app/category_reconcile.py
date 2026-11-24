"""类目逐笔对账 (2026-11-03)

背景: 写端 `check_expense_list_categories` 只算「车费/住宿/其他车费/其他」4 个类别的
**总额**（清单列 vs 发票分类 vs 飞书表单），报错只有一句
    「车费: 清单3925.0 ≠ 发票4131.0」
看不到差在哪几笔，人工要翻十几张票。

本模块在**读端**独立重算，按类别把「清单行」与「发票」逐笔配对，输出
    matched / list_only(只在清单) / invoice_only(只在发票)
这样点开就能看到"哪两笔对不上"。读端计算的好处: 不改库、不重跑审核、历史单立刻可用。

匹配策略(贪心两轮):
    第 1 轮: 金额相等(±0.01) **且** 起止地名对得上 → 配对
    第 2 轮: 仅金额相等 → 配对
    剩余: 记为单边(及差额来源)
总额一致但逐笔配不上时(合并开票/拆分开票), 标注说明, 不制造噪音。
"""
from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional

# ---- 与写端口径保持一致(checker/expense_list_checker.py) ----
INVOICE_CATEGORY_RULES = [
    ('住宿', ['住宿']),
    ('其他车费', ['客运服务费', '乘车费', '城市轨道交通', '地铁', '汽油', '乙醇汽油',
                  '通行费', '租车', '车辆租赁', '汽车租赁']),
    ('车费', ['旅客运输服务', '铁路', '火车', '高铁', '航空', '机票', '客票款', '旅客票务']),
]
CATEGORY_LABELS = ('车费', '住宿', '其他车费', '其他')
# 清单列名 = 类别名
LIST_COLUMN = {'车费': 'transport', '住宿': 'hotel', '其他车费': 'other_transport', '其他': 'other'}
# 飞书表单字段别名
FORM_FIELD_ALIASES = {
    '车费': ['车费'],
    '住宿': ['住宿费', '住宿费用'],
    '其他车费': ['其他车费/燃油费', '其他车费', '燃油费'],
    '其他': ['其他费用', '其他'],
}

_EPS = 0.01
# 单类目最多回传的行数(避免明细过多撑爆响应)
_MAX_ROWS = 40


def _r2(x: Any) -> float:
    try:
        return round(float(x or 0), 2)
    except (TypeError, ValueError):
        return 0.0


def classify_invoice_category(inv_data: Dict) -> str:
    """发票 → 四类之一(与写端口径一致)。items 兼容 dict/str 两种形态。"""
    text = ''
    items = inv_data.get('items') or []
    if items:
        first = items[0]
        if isinstance(first, dict):
            text = str(first.get('name') or first.get('项目名称') or '')
        elif isinstance(first, str):
            text = first
        if not text:
            text = ' '.join(str(x) for x in items if x)
    text = text + str(inv_data.get('invoice_type') or '')
    for cat, kws in INVOICE_CATEGORY_RULES:
        if any(kw in text for kw in kws):
            return cat
    return '其他'


def _invoice_data_of(inv: Dict) -> Dict:
    """取发票的 invoice_data: 优先 raw_json.invoice_data, 回退 ORM 列。"""
    data: Dict = {}
    raw = inv.get('raw_json')
    if raw:
        try:
            obj = json.loads(raw) if isinstance(raw, str) else raw
            if isinstance(obj, dict):
                data = obj.get('invoice_data') or {}
        except Exception:
            data = {}
    if not isinstance(data, dict):
        data = {}
    # 回退/补齐 ORM 列
    data.setdefault('invoice_type', inv.get('invoice_type') or '')
    if not data.get('total_amount'):
        data['total_amount'] = inv.get('total_amount')
    return data


def _norm_place(s: str) -> str:
    """地名归一: 去'站/市/东/西/南/北'等后缀噪音, 便于包含匹配。"""
    s = re.sub(r'[\s　]', '', str(s or ''))
    s = re.sub(r'(站|市|机场|国际|航站楼)$', '', s)
    return s


def _route_hit(list_row: Dict, inv_text: str) -> bool:
    """清单行的起止地名是否都出现在发票文本里(用于同金额多候选时择优)。"""
    frm = _norm_place(list_row.get('from_place'))
    to = _norm_place(list_row.get('to_place'))
    if not (frm and to):
        return False
    t = str(inv_text or '')
    # 去掉"站"再比, 兼容 '乌兰察布站-清河站'
    t_norm = t.replace('站', '')
    return (frm in t_norm) and (to in t_norm)


def _invoice_desc(inv_data: Dict) -> str:
    parts = []
    if inv_data.get('travel_info'):
        parts.append(str(inv_data['travel_info']))
    items = inv_data.get('items') or []
    if items:
        first = items[0]
        if isinstance(first, dict):
            nm = first.get('name') or first.get('项目名称')
            if nm:
                parts.append(str(nm))
        elif isinstance(first, str):
            parts.append(first)
    if not parts:
        parts.append(str(inv_data.get('invoice_type') or ''))
    pax = inv_data.get('passenger_name')
    if pax:
        parts.append(f'({pax})')
    return ' '.join(p for p in parts if p)[:80]


def _form_sums(raw_data) -> Dict[str, Optional[float]]:
    """飞书表单 4 类金额; 字段缺失返回 None(区别于 0)。"""
    out: Dict[str, Optional[float]] = {c: None for c in CATEGORY_LABELS}
    if not raw_data:
        return out
    try:
        rd = json.loads(raw_data) if isinstance(raw_data, str) else raw_data
    except Exception:
        return out
    if not isinstance(rd, dict):
        return out
    inst = rd.get('approval_instance') or {}
    form = inst.get('form') if isinstance(inst, dict) else None
    if isinstance(form, str):
        try:
            form = json.loads(form)
        except Exception:
            form = None
    if not isinstance(form, list):
        return out
    fields = {f.get('name'): f.get('value') for f in form if isinstance(f, dict)}
    for cat, aliases in FORM_FIELD_ALIASES.items():
        for name in aliases:
            if name in fields:
                try:
                    out[cat] = round(float(fields[name] or 0), 2)
                except (TypeError, ValueError):
                    out[cat] = None
                break
    return out


def build_category_reconcile(invoices: List[Dict], list_items: List[Dict],
                             raw_data=None) -> Dict:
    """返回 {categories:[...], summary:{...}}，供看板逐笔对账弹框使用。

    Args:
        invoices: 发票行(dict, 需含 raw_json/invoice_type/total_amount/file_name/invoice_number)
        list_items: 清单行(dict, 含 date/from_place/to_place/item_type/transport/hotel/other_transport/other)
        raw_data: 审批 raw_data(取表单 4 类金额)
    """
    form = _form_sums(raw_data)

    # 只统计正式发票(file_type='invoice')
    inv_rows = [i for i in invoices if (i.get('file_type') or '') == 'invoice']

    # 发票按类别聚合(带明细)
    inv_by_cat: Dict[str, List[Dict]] = {c: [] for c in CATEGORY_LABELS}
    for inv in inv_rows:
        data = _invoice_data_of(inv)
        amt = _r2(data.get('total_amount') or inv.get('total_amount'))
        if amt <= 0:
            continue
        cat = classify_invoice_category(data)
        inv_by_cat.setdefault(cat, []).append({
            'amount': amt,
            'desc': _invoice_desc(data),
            'invoice_number': inv.get('invoice_number') or '',
            'invoice_type': data.get('invoice_type') or inv.get('invoice_type') or '',
            'file_name': inv.get('file_name') or '',
            'raw_text': str(data.get('travel_info') or '') + ' ' + str(data.get('items') or ''),
            '_used': False,
        })

    categories = []
    for cat in CATEGORY_LABELS:
        col = LIST_COLUMN[cat]
        # 清单该类明细
        list_rows = []
        for li in list_items or []:
            amt = _r2(li.get(col))
            if amt <= 0:
                continue
            desc_bits = [str(li.get('date') or '')[:10]]
            # 兼容两种键名: 8002 ListItem.to_dict 用 from/to/type; 写端原始数据用 from_place/to_place/item_type
            frm = li.get('from_place') or li.get('from') or ''
            to = li.get('to_place') or li.get('to') or ''
            itype = li.get('item_type') or li.get('type') or ''
            if frm or to:
                desc_bits.append(f'{frm}→{to}')
            if itype:
                desc_bits.append(str(itype))
            if li.get('remark'):
                desc_bits.append(str(li['remark']))
            list_rows.append({
                'amount': amt,
                'desc': ' '.join(b for b in desc_bits if b)[:80],
                'date': str(li.get('date') or '')[:10],
                'from_place': frm, 'to_place': to,
                'item_type': itype,
                'remark': str(li.get('remark') or ''),
                '_used': False,
            })

        invs = inv_by_cat.get(cat, [])
        matched = []
        # 第 1 轮: 金额 + 路线都对得上
        for lr in list_rows:
            if lr['_used']:
                continue
            for iv in invs:
                if iv['_used'] or abs(iv['amount'] - lr['amount']) > _EPS:
                    continue
                if _route_hit(lr, iv['raw_text']):
                    lr['_used'] = iv['_used'] = True
                    matched.append({'list': _strip(lr), 'invoice': _strip(iv), 'by': 'amount+route'})
                    break
        # 第 2 轮: 仅金额
        for lr in list_rows:
            if lr['_used']:
                continue
            for iv in invs:
                if iv['_used'] or abs(iv['amount'] - lr['amount']) > _EPS:
                    continue
                lr['_used'] = iv['_used'] = True
                matched.append({'list': _strip(lr), 'invoice': _strip(iv), 'by': 'amount'})
                break

        list_only = [_strip(x) for x in list_rows if not x['_used']]
        invoice_only = [_strip(x) for x in invs if not x['_used']]

        list_total = _r2(sum(x['amount'] for x in list_rows))
        inv_total = _r2(sum(x['amount'] for x in invs))
        form_total = form.get(cat)
        diff = _r2(inv_total - list_total)
        totals_match = abs(diff) < _EPS
        lo_sum = _r2(sum(x['amount'] for x in list_only))
        io_sum = _r2(sum(x['amount'] for x in invoice_only))

        if totals_match:
            note = ('总额一致' if not (list_only or invoice_only)
                    else '总额一致；逐笔未能一一对应（可能合并/拆分开票，不影响总额）')
        elif list_only and invoice_only:
            note = f'差额 {diff:+.2f} = 只在清单 {lo_sum:.2f} − 只在发票 {io_sum:.2f}'
        elif list_only:
            note = f'清单多出 {lo_sum:.2f}，发票中找不到对应票据'
        else:
            note = f'发票多出 {io_sum:.2f}，清单中未登记'

        categories.append({
            'category': cat,
            'list': list_total,
            'invoice': inv_total,
            'form': form_total,
            'diff': diff,
            'passed': totals_match,
            # mode: diff=总额不一致(需要关注) / info=总额一致(逐笔仅参考)
            'mode': 'info' if totals_match else 'diff',
            'form_passed': (None if form_total is None else abs(form_total - inv_total) < _EPS),
            'list_count': len(list_rows),
            'invoice_count': len(invs),
            'matched': matched[:_MAX_ROWS],
            'list_only': list_only[:_MAX_ROWS],
            'invoice_only': invoice_only[:_MAX_ROWS],
            'list_only_total': len(list_only),
            'invoice_only_total': len(invoice_only),
            'list_only_sum': lo_sum,
            'invoice_only_sum': io_sum,
            'note': note,
        })

    failed = [c['category'] for c in categories if not c['passed']]
    return {
        'categories': categories,
        'summary': {
            'failed_categories': failed,
            'passed': not failed,
            'total_matched': sum(len(c['matched']) for c in categories),
            'total_list_only': sum(len(c['list_only']) for c in categories),
            'total_invoice_only': sum(len(c['invoice_only']) for c in categories),
        },
    }


def _strip(row: Dict) -> Dict:
    """去掉内部标记字段, 供前端使用。"""
    return {k: v for k, v in row.items() if not k.startswith('_')}
