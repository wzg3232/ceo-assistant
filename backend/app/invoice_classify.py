"""
发票五分类 + 非发票剔除 共享逻辑 (2026-11-23 抽离, main_v3 与 invoice_export 共用, 单一来源)
=====================================================================================
分类: 增值税专用发票 / 铁路电子客票 / 航空运输电子客票行程单 / 旅客运输服务 / 增值税普通发票 / 车辆通行费
      非标准票面归 '其他' (有效的通行费电子票据单独归 '车辆通行费')
剔除: 餐饮发票 / ETC行程单 / 住宿发票(水单) / 文件名含"支付记录" / 通行费发票中票面为
      通行单/行程单(ETC行程记录) 的 / document_category='list' 的支撑清单
      (网约车/曹操/高德行程单, 借款冲抵, 费用说明, 结算单; 无票号, 非有效票)
      → 非发票, 不下发不入导出
"""
import json
from typing import Optional

_TRANSIT_KW = ('旅客运输', '客运', '交通运输', '出租车', '城市轨道', '城市公共')
_TRANSIT_STRONG = ('网约车', '出租车', '旅客运输')


def classify_invoice(d: dict, raw_json: Optional[str] = None) -> str:
    """返回分类: 专票/铁路/航空/旅客运输服务/普票, 非标准票面归'其他'。"""
    title = (d.get('invoice_type') or '').strip()
    if '专用发票' in title:
        return '增值税专用发票'
    if '铁路' in title and '客票' in title:
        return '铁路电子客票'
    if '航空' in title:
        return '航空运输电子客票行程单'
    items_blob = ''
    if raw_json:
        try:
            raw = json.loads(raw_json)
            inv = raw.get('invoice_data') or {}
            items_blob = json.dumps(inv.get('items') or [], ensure_ascii=False)
        except Exception:
            items_blob = ''
    if '普通发票' in title:
        return '旅客运输服务' if any(k in items_blob for k in _TRANSIT_KW) else '增值税普通发票'
    # 非标准票面标题 (住宿发票/通行费发票/餐饮发票...):
    # 强运输关键词定类, 其余归"其他"
    if any(k in (title + items_blob) for k in _TRANSIT_STRONG):
        return '旅客运输服务'
    if title == '通行费发票':
        # 有效的通行费电子票据 (省级财政电子票据, 票面 通行费电子票据/通行费票据(电子));
        # 票面为 通行单/行程单 的 ETC 行程记录已在 is_not_invoice 上游剔除, 不会走到这里
        return '车辆通行费'
    return '其他'


def is_not_invoice(d: dict, raw_json: Optional[str] = None) -> bool:
    """非发票文件判定 (DB保留, 仅在下发/导出时剔除)。"""
    t = (d.get('invoice_type') or '').strip()
    fn = d.get('file_name') or ''
    if t in ('餐饮发票', 'ETC行程单', '住宿发票'):
        return True
    if '支付记录' in fn:
        return True
    # 支撑清单 (网约车/曹操/高德 行程单、借款冲抵、费用说明、结算单等,
    # document_category='list', 无票号/非有效票) → 非发票 (2026-11-23 拍板, 同 ETC行程单 剔除)
    _cat = ''
    if raw_json:
        try:
            _cat = ((json.loads(raw_json) or {}).get('invoice_data') or {}).get('document_category') or ''
        except Exception:
            _cat = ''
    if _cat == 'list':
        return True
    if t == '通行费发票':
        if raw_json:
            try:
                _r = json.loads(raw_json)
                _inv = _r.get('invoice_data') or {}
                _title = (_inv.get('invoice_type') or _inv.get('title') or '')
                if '通行单' in _title or '行程单' in _title:
                    return True
            except Exception:
                pass
    return False


def invoice_data_of(raw_json: Optional[str]) -> dict:
    """raw_json → invoice_data 子块 (取不到返回 {})。"""
    if not raw_json:
        return {}
    try:
        raw = json.loads(raw_json)
        return raw.get('invoice_data') or {}
    except Exception:
        return {}
