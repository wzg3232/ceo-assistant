"""
发票导出 (2026-11-23 重构: 扁平结构, 固定 6 类PDF + 1 总Excel)
================================================
输入: 前端筛选后的发票 invoice id 列表 (DB 主键, 含任意日期范围)
输出: 单个外层 zip (命名: 单日期 "YYYY-MM-DD发票导出.zip", 跨日期 "起始~结束发票导出.zip"),
      内部固定 8 个文件 + 1 个航空 OFD/XML 原件文件夹:
        1. 增值税专用发票.pdf          范围内全部该类发票合并 (按开票日期排序, 纯发票原件, 无标注页)
        2. 铁路电子客票.pdf
        3. 航空运输电子客票行程单.pdf   仅 PDF 原件合并; 无 PDF 时为占位说明页
        4. 旅客运输服务.pdf
        5. 增值税普通发票.pdf
        6. 车辆通行费.pdf
        7. 其他.pdf
        8. 发票汇总.xlsx              范围内全部发票信息 (12列, 含上面任一类的全部行)
        航空运输电子客票行程单-OFD XML原件/   航空类发票 OFD/XML 原件 (2026-11-23 新增):
          每张 OFD 原件放一份 "审批编号__票名.ofd" + 其内嵌 XML (数电票XML版)
          "审批编号__票名-XML.zip" (内嵌全部 XML, 与列表页单票"XML"下载同款结构);
          若有独立 .xml 原件则按 "审批编号__票名.xml" 放入; 范围内无该类 OFD/XML 原件时此文件夹不产出。
      7 类 PDF 恒各出一个: 范围内该类无 PDF 原件 (或原件全部解析失败) 时,
      该 PDF 为 1 页占位说明页 ("本范围内无该类发票 PDF 原件"), 不造假数据。

发票类型 = 后端七分类 (专票/铁路/航空行程单/旅客运输/普票/车辆通行费) + 其他兜底 (app.invoice_classify, 与 /api/approvals 列表同一套逻辑)。
非发票文件 (餐饮/ETC行程单/住宿水单/支付记录) 自动剔除, 与列表一致。
导出 = 各类 PDF 原件合并进同类 PDF; 航空类行程单另附上述 OFD/XML 原件文件夹
      (航空原件多为 OFD: 有 PDF 的照旧合入类 PDF, 没有 PDF 的只整理 OFD/XML 进该文件夹)。
      其他类不产出 OFD/XML (税局 RPA 批量下载另做)。

Excel 列 (用户口径, 12列):
  审批编号 | 申请人 | 审批类型 | 发票类型 | 文件名 | 发票号 | 开票日期 | 购买方 | 销售方(有的话带上) | 金额 | 税额 | 价税合并(小写数字)
  金额=不含税(取不到时=价税合计), 税额=raw_json.tax_amount(取不到留空), 价税合并=total_amount 数字。

端点不变:
  POST /api/invoices/export                  -> {"task_id": ...}
  GET  /api/invoices/export/{task_id}        -> 进度 (前端轮询)
  GET  /api/invoices/export/{task_id}/download -> FileResponse(外层zip)

原件获取: 本地缓存 backend/files/{serial}__{原文件名} 优先;
缓存 miss → 飞书回源 (live API 实时 attachmentV2 URL 优先, raw_data 旧 URL 兜底),
与 main_v3.py /files 端点同一套逻辑, 回源成功写回缓存。
"""
import io
import json
import shutil
import threading
import uuid
import zipfile
from datetime import datetime, timedelta
from pathlib import Path

from app.database import SessionLocal
from app.models import Approval, Invoice
from app.invoice_classify import classify_invoice, is_not_invoice, invoice_data_of

# ---------------- 任务注册表 (单worker进程内, 重启即清空; 完成品保留24h可下载) ----------------
_tasks: dict = {}
_tasks_lock = threading.Lock()
EXPORT_ROOT = Path("/tmp/ceo-export")

# 分类顺序 (扁平导出 7 类; "其他" 在最后): 与前端筛选下拉一致
CLASS_ORDER = ['增值税专用发票', '铁路电子客票', '航空运输电子客票行程单', '旅客运输服务', '增值税普通发票', '车辆通行费', '其他']

# Excel 列 (用户口径: 销售方信息有的话就带上)
EXCEL_HEADERS = ['审批编号', '申请人', '审批类型', '发票类型', '文件名', '发票号',
                 '开票日期', '购买方', '销售方', '金额', '税额', '价税合并（小写）']


def _new_task() -> str:
    tid = uuid.uuid4().hex[:12]
    _tasks[tid] = {
        "id": tid,
        "status": "pending",      # pending / running / done / error
        "progress": 0,
        "current": "排队中",
        "errors": [],
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "filename": "",
        "path": str(EXPORT_ROOT / tid),   # workdir (内部字段, 公开状态不外泄)
    }
    with _tasks_lock:
        now = datetime.now()
        for k, v in list(_tasks.items()):
            if v["status"] in ("done", "error") and v.get("finished_at"):
                try:
                    if now - datetime.fromisoformat(v["finished_at"]) > timedelta(hours=24):
                        _tasks.pop(k, None)
                        shutil.rmtree(EXPORT_ROOT / k, ignore_errors=True)
                except Exception:
                    _tasks.pop(k, None)
                    shutil.rmtree(EXPORT_ROOT / k, ignore_errors=True)
    return tid


def _set(tid: str, **kw):
    with _tasks_lock:
        _tasks[tid].update(kw)


def _task_state(tid: str):
    """内部: 含 path 的完整状态 (下载端点用)."""
    with _tasks_lock:
        t = _tasks.get(tid)
        return dict(t) if t else None


def get_task(tid: str):
    """公开: 去掉内部 path 字段 (状态轮询用)."""
    d = _task_state(tid)
    if not d:
        return None
    d.pop("path", None)
    return d


# ---------------- 飞书回源 (与 /files 端点同款, 返回 bytes 而非 FileResponse) ----------------
FILES_DIR = (Path(__file__).parent.parent / "files").resolve()


def _safe_name(s: str) -> str:
    return s.replace("\\", "_").replace("/", "_")


def fetch_invoice_file(serial: str, filename: str, approval_id: int) -> bytes:
    """本地缓存优先; miss 时飞书回源. 失败抛 RuntimeError."""
    p = FILES_DIR / f"{_safe_name(serial)}__{filename}"
    if p.is_file():
        return p.read_bytes()

    import requests as _rq
    from app.api.webhook import get_feishu_token, get_approval_status

    db = SessionLocal()
    try:
        _ap = db.query(Approval).filter(Approval.id == approval_id).first()
    finally:
        db.close()
    if not _ap or not _ap.raw_data:
        raise RuntimeError(f"无回源数据: {serial}/{filename}")

    dl_url = None
    # ① live API: 实时取该实例最新 attachmentV2 URL (authcode 实时生成, 老单也能拉)
    try:
        _live = get_approval_status(_ap.approval_id)
    except Exception:
        _live = None
    if _live:
        _lform = _live.get("form")
        if isinstance(_lform, str):
            try:
                _lform = json.loads(_lform)
            except Exception:
                _lform = None
        for _f in (_lform or []):
            if not isinstance(_f, dict) or _f.get("type") != "attachmentV2":
                continue
            _lnames = [x.strip() for x in str(_f.get("ext") or "").split(",")]
            _lurls = _f.get("value") or []
            if isinstance(_lurls, str):
                _lurls = [_lurls]
            for _i, _nm in enumerate(_lnames):
                if _nm == filename and _i < len(_lurls):
                    dl_url = _lurls[_i]
                    break
            if dl_url:
                break
    # ② 兜底: raw_data 里存的旧 URL
    if not dl_url:
        _rd = _ap.raw_data if isinstance(_ap.raw_data, dict) else json.loads(_ap.raw_data)
        _inst = _rd.get("approval_instance") or {}
        _form = _inst.get("form")
        if isinstance(_form, str):
            _form = json.loads(_form)
        for _f in (_form or []):
            if not isinstance(_f, dict) or _f.get("type") != "attachmentV2":
                continue
            _names = [x.strip() for x in str(_f.get("ext") or "").split(",")]
            _urls = _f.get("value") or []
            if isinstance(_urls, str):
                _urls = [_urls]
            for _i, _nm in enumerate(_names):
                if _nm == filename and _i < len(_urls):
                    dl_url = _urls[_i]
                    break
            if dl_url:
                break
    if not dl_url:
        raise RuntimeError(f"未找到可回源 URL: {serial}/{filename}")

    hdrs = {}
    try:
        _tk = get_feishu_token()
        if _tk:
            hdrs["Authorization"] = f"Bearer {_tk}"
    except Exception:
        pass
    _resp = _rq.get(dl_url, headers=hdrs, timeout=60)
    if _resp.status_code != 200 or not _resp.content:
        raise RuntimeError(f"飞书回源失败 HTTP {_resp.status_code}: {filename}")
    # 写回缓存
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(_resp.content)
    return _resp.content


# ---------------- OFD → XML 提取 (OFD 本质是 zip, 内含 ofd.xml 等; 仅供单张下载端点用) ----------------
def extract_ofd_xmls(ofd_bytes: bytes) -> list:
    out = []
    try:
        with zipfile.ZipFile(io.BytesIO(ofd_bytes)) as z:
            for n in z.namelist():
                if n.lower().endswith(".xml") and not n.endswith("/"):
                    out.append((n, z.read(n)))
    except Exception:
        pass
    return out


# ---------------- 中文占位页 (reportlab CID 字体 STSong-Light, 无需 ttf 文件) ----------------
_font_ready = None

# 日期归一化 (库内 invoice_date 混合格式: 2026-07-10 / 2026/07/10 / 2026年07月10日 / 仅到月 / Excel序列值)
def _norm_date(d):
    import re
    from datetime import datetime, timedelta
    s = str(d or "").strip()
    m = re.fullmatch(r"(\d{4})[-/年.](\d{1,2})[-/月.](\d{1,2})日?", s)
    if m:
        return int(m[1]) * 10000 + int(m[2]) * 100 + int(m[3])
    m = re.fullmatch(r"(\d{4})[-/年.](\d{1,2})月?", s)
    if m:  # 仅到月 → 当月01日
        return int(m[1]) * 10000 + int(m[2]) * 100 + 1
    if re.fullmatch(r"\d{5}", s) and 30000 < int(s) < 60000:  # Excel 序列值
        try:
            dt = datetime(1899, 12, 30) + timedelta(days=int(s))
            return dt.year * 10000 + dt.month * 100 + dt.day
        except Exception:
            return None
    return None


def _norm_to_iso(v: int) -> str:
    return f"{v // 10000}-{(v // 100) % 100:02d}-{v % 100:02d}"


# ---------------- 主任务体 (扁平: 6 类PDF + 1 总Excel, 无按日期文件夹) ----------------
def run_export_task(tid: str, invoice_ids: list):
    try:
        _do_export(tid, invoice_ids)
    except Exception as e:
        import traceback
        with _tasks_lock:
            _tasks[tid]["errors"].append(traceback.format_exc()[-500:])
        _set(tid, status="error", progress=100, current=f"导出失败: {e}",
             finished_at=datetime.now().isoformat(timespec="seconds"))


def _dk(it: dict):
    """排序键: 无日期排最后."""
    return (1, it['date_key']) if it['date_key'] == '无日期' else (0, it['date_key'])


def _make_placeholder_pdf(cls: str, note: str = "") -> bytes:
    """占位页: 该分类在范围内无可用 PDF 原件时, 也输出 1 页说明 (保证 6 类 PDF 恒存在)."""
    from reportlab.pdfgen import canvas as _canvas
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.cidfonts import UnicodeCIDFont
    global _font_ready
    if _font_ready is None:
        try:
            pdfmetrics.registerFont(UnicodeCIDFont("STSong-Light"))
        except Exception:
            pass
        _font_ready = True
    w, h = 595.0, 842.0
    buf = io.BytesIO()
    c = _canvas.Canvas(buf, pagesize=(w, h))
    c.setFont("STSong-Light", 14)
    c.setFillColorRGB(0.4, 0.4, 0.4)
    c.drawCentredString(w / 2, h * 0.52, f"{cls}")
    c.setFont("STSong-Light", 11)
    c.drawCentredString(w / 2, h * 0.47, "（本范围内无该类发票 PDF 原件）")
    if note:
        c.setFont("STSong-Light", 9)
        c.setFillColorRGB(0.55, 0.55, 0.55)
        for i, line in enumerate(note.split("\n")):
            c.drawCentredString(w / 2, h * 0.42 - i * 16, line)
    c.save()
    return buf.getvalue()


# ---------------- 行程单/流水页特征 (网约车合并PDF里拼的滴滴TRIP TABLE/行程单页) ----------------
# 注意: 只用网约车流水页专属特征词, 不用 "行程单" 泛词 —— 航空类PDF页内也含 "航空运输电子客票行程单" 字样, 会被误杀
_JUNK_PAGE_MARKERS = ('TRIP TABLE', '行程起止日期')
# 页面上印刷的发票号 (pypdf 读不出值时由 pdfplumber 兜底), 用于"页级票号筛选"
import re as _re
_PAGE_INVOICE_NO_RE = _re.compile(r'发票号码[:：\s]{0,6}(\d{8,24})')


def _page_is_junk(pg) -> bool:
    try:
        t = (pg.extract_text() or '').lower()
    except Exception:
        return False  # 读不出文本的页保留, 不误删
    return any(m.lower() in t for m in _JUNK_PAGE_MARKERS)


def _page_invoice_numbers(text: str) -> list:
    return _PAGE_INVOICE_NO_RE.findall(text or '')


def _file_page_texts(data: bytes, reader) -> list:
    """逐页文本 (pypdf 优先; 读不到发票号时用 pdfplumber 补读该页, pypdf 缺 ToUnicode 字体的页值丢失)."""
    texts = []
    pb = None
    for i, pg in enumerate(reader.pages):
        try:
            t = pg.extract_text() or ''
        except Exception:
            t = ''
        if not _PAGE_INVOICE_NO_RE.search(t):
            try:
                if pb is None:
                    import pdfplumber
                    pb = pdfplumber.open(io.BytesIO(data))
                if i < len(pb.pages):
                    pb_t = pb.pages[i].extract_text() or ''
                    if _PAGE_INVOICE_NO_RE.search(pb_t):
                        t = pb_t
            except Exception:
                pb = None
        texts.append(t)
    if pb:
        pb.close()
    return texts


def _merge_class_pdf(rows: list, data_map: dict, errors: list) -> bytes:
    """合并一类发票的PDF原件 (纯原件, 不插标注页), 返回 PDF bytes; 全部失败返回空 bytes.
    网约车平台常把 多张发票+行程单 拼进同一个PDF上传, 且一个文件可能被多条发票记录指向:
      ① 同 (审批编号, 文件名) 源文件只合并一次 (不再按记录数重复合并);
      ② 行程单/流水页 (TRIP TABLE 等特征) 直接丢弃;
      ③ 页级票号筛选: 页面上印的发票号 ∉ 该文件所选记录票号集合的页丢弃
         (典型: 合并文件里拼了开票日期在导出范围外的发票页), 票号不可判的页保留不误删."""
    from pypdf import PdfReader, PdfWriter
    writer = PdfWriter()
    n_pages = 0
    seen_files: set = set()
    allowed_by_file: dict = {}
    for it in rows:
        key = (it['serial'], it['file_name'])
        allowed_by_file.setdefault(key, set()).add(str(it.get('invoice_number') or '').strip())
    for it in rows:
        key = (it['serial'], it['file_name'])
        if key in seen_files:
            continue
        seen_files.add(key)
        data = data_map.get(it['id'])
        if not data:
            continue
        allowed = {n for n in allowed_by_file.get(key, set()) if n}
        try:
            r = PdfReader(io.BytesIO(data))
            if r.is_encrypted:
                r.decrypt("")
            page_texts = _file_page_texts(data, r)
            for idx, pg in enumerate(r.pages):
                if _page_is_junk(pg):
                    continue
                nos = _page_invoice_numbers(page_texts[idx])
                if nos and allowed and not any(n in allowed for n in nos):
                    continue  # 该页发票号不在所选记录内 (如开票日期超出导出范围) → 丢弃
                writer.add_page(pg)
                n_pages += 1
        except Exception:
            errors.append(f"{it['serial']} {it['file_name']}: PDF解析失败, 未并入")
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue() if n_pages or any(data_map.get(it['id']) is not None for it in rows) else b''


def _write_excel(rows: list) -> bytes:
    """范围内全部发票 发票汇总.xlsx (openpyxl, 12列用户口径)."""
    from openpyxl import Workbook
    from openpyxl.styles import Font
    from openpyxl.utils import get_column_letter

    wb = Workbook()
    ws = wb.active
    ws.title = "发票汇总"
    ws.append(EXCEL_HEADERS)
    for c in ws[1]:
        c.font = Font(bold=True)
    for it in rows:
        ws.append([
            it['serial'], it['applicant'], it['approval_type'], it['cls'],
            it['file_name'], it['invoice_number'], it['invoice_date'],
            it['buyer'] or '', it['seller'] or '',
            it['amount'], it['tax'] if it['tax'] is not None else '',
            it['total'],
        ])
    # 列宽 + 数字格式
    widths = [14, 10, 12, 22, 30, 24, 12, 24, 24, 10, 10, 14]
    for i, w in enumerate(widths, 1):
        ws.column_dimensions[get_column_letter(i)].width = w
    for row in ws.iter_rows(min_row=2, min_col=10, max_col=12):
        for c in row:
            if isinstance(c.value, (int, float)):
                c.number_format = '#,##0.00'
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _do_export(tid: str, invoice_ids: list):
    _set(tid, status="running", progress=1, current="读取发票数据")
    workdir = Path(_task_state(tid)["path"])
    workdir.mkdir(parents=True, exist_ok=True)

    db = SessionLocal()
    try:
        invs = (db.query(Invoice)
                .filter(Invoice.id.in_(invoice_ids))
                .order_by(Invoice.id.asc())
                .all())
        ap_map = {}
        if invs:
            aids = {i.approval_id for i in invs}
            for a in db.query(Approval).filter(Approval.id.in_(aids)).all():
                ap_map[a.id] = a
    finally:
        db.close()
    if not invs:
        raise RuntimeError("未找到任何发票记录")

    # ---- 逐张取数: 分类 + 非发票剔除 + 税额/销售方/日期 (raw_json) ----
    items, skipped = [], []
    for inv in invs:
        ap = ap_map.get(inv.approval_id)
        serial = ap.serial_number if ap else f"inv{inv.id}"
        invd = {'invoice_type': inv.invoice_type, 'file_name': inv.file_name}
        if is_not_invoice(invd, inv.raw_json):
            skipped.append(f"{serial} {inv.file_name} (非发票, 已剔除)")
            continue
        x = invoice_data_of(inv.raw_json)
        tax = x.get('tax_amount')
        if tax in (None, ''):
            tax = None
        else:
            try:
                tax = round(float(tax), 2)
            except Exception:
                tax = None
        try:
            total_f = round(float(inv.total_amount or 0), 2)
        except Exception:
            total_f = 0.0
        # 金额列口径: 不含税金额取不到时用 价税合计 - 税额
        amt_raw = x.get('amount_without_tax')
        try:
            amount = round(float(amt_raw), 2) if amt_raw not in (None, '') else None
        except Exception:
            amount = None
        if amount is None:
            amount = total_f if tax is None else round(total_f - tax, 2)
        # 销售方: 有的话带上税号
        seller = x.get('seller_name') or ''
        if x.get('seller_tax_id'):
            seller = f"{seller} (税号:{x['seller_tax_id']})" if seller else f"(税号:{x['seller_tax_id']})"
        v = _norm_date(inv.invoice_date or x.get('invoice_date'))
        items.append({
            'id': inv.id,
            'serial': serial,
            'applicant': ap.applicant_name if ap else '',
            'approval_type': ap.approval_type if ap else '',
            'cls': classify_invoice(invd, inv.raw_json),
            'file_name': inv.file_name,
            'invoice_number': inv.invoice_number or '',
            'invoice_date': inv.invoice_date or x.get('invoice_date') or '',
            'date_key': _norm_to_iso(v) if v else '无日期',
            'buyer': inv.buyer_name or x.get('buyer_name') or '',
            'seller': seller,
            'amount': amount,
            'tax': tax,
            'total': total_f,
            'ext': inv.file_name.rsplit('.', 1)[-1].lower() if '.' in inv.file_name else '',
            'approval_id': inv.approval_id,
        })
    if not items:
        raise RuntimeError("所选范围内无有效发票 (均为非发票文件): " + "; ".join(skipped[:5]))

    # ---- 回源取 PDF 原件 (仅 PDF 参与合并; OFD 原件不取, 仅入 Excel) ----
    pdf_items = [it for it in items if it['ext'] == 'pdf']
    n = len(pdf_items)
    data_map: dict = {}
    fetch_errors = []
    for i, it in enumerate(pdf_items):
        pct = int(5 + 55 * (i + 1) / max(n, 1))
        _set(tid, progress=pct, current=f"回源取原件 {i + 1}/{n}: {it['serial']} {it['file_name']}")
        try:
            data_map[it['id']] = fetch_invoice_file(it['serial'], it['file_name'], it['approval_id'])
        except Exception as e:
            fetch_errors.append(f"{it['serial']} {it['file_name']}: {str(e)[:80]}")
            with _tasks_lock:
                _tasks[tid]["errors"].append(f"回源失败 {it['serial']} {it['file_name']}: {e}")

    # ---- 航空类 OFD/XML 原件回源 (航空原件多为 OFD, 飞书无独立 XML 附件; XML 取自 OFD 内嵌解出) ----
    AIR_CLS = '航空运输电子客票行程单'
    air_folder = f"{AIR_CLS}-OFD XML原件"
    air_aux = [it for it in items if it['cls'] == AIR_CLS and it['ext'] in ('ofd', 'xml')]
    aux_map: dict = {}
    na = len(air_aux)
    for i, it in enumerate(air_aux):
        _set(tid, progress=60 + int(15 * (i + 1) / na),
             current=f"取航空 OFD/XML 原件 {i + 1}/{na}: {it['serial']} {it['file_name']}")
        try:
            aux_map[it['id']] = fetch_invoice_file(it['serial'], it['file_name'], it['approval_id'])
        except Exception as e:
            fetch_errors.append(f"{it['serial']} {it['file_name']}: OFD/XML原件回源失败 ({str(e)[:60]})")
            with _tasks_lock:
                _tasks[tid]["errors"].append(f"OFD/XML回源失败 {it['serial']} {it['file_name']}: {e}")

    # ---- 扁平: 6 类 PDF 恒输出 (有则合并原件, 无则占位说明页), + 1 个总 Excel ----
    zip_entries: list = []
    for ci, cls in enumerate(CLASS_ORDER):
        cat_rows = [it for it in items if it['cls'] == cls and it['ext'] == 'pdf']
        _set(tid, progress=75 + int(15 * ci / len(CLASS_ORDER)),
             current=f"生成 {cls} PDF ({len(cat_rows)} 张)" + ("" if cat_rows else " [占位]"))
        if cat_rows:
            cat_rows.sort(key=lambda r: (_dk(r), r['serial'], r['file_name']))
            pdf_bytes = _merge_class_pdf(cat_rows, data_map, fetch_errors)
        else:
            pdf_bytes = b''
        if not pdf_bytes:
            # 无该类PDF原件, 或原件全部解析失败 → 占位页 (标注失败明细)
            note = ""
            if cat_rows:
                fails = [e for e in fetch_errors if any(f"{it['serial']} {it['file_name']}" in e for it in cat_rows)]
                note = "原件解析/回源失败明细:\n" + "\n".join(fails[:5])
            elif cls == AIR_CLS and air_aux:
                note = f"本类无 PDF 原件; OFD/XML 原件见 '{air_folder}' 文件夹"
            pdf_bytes = _make_placeholder_pdf(cls, note)
        zip_entries.append((f"{cls}.pdf", pdf_bytes))

    # 总 Excel: 范围内全部发票 (含非PDF原件), 按 开票日期, 类别, 编号 排序
    all_rows = sorted(items, key=lambda r: (_dk(r),
                                            CLASS_ORDER.index(r['cls']) if r['cls'] in CLASS_ORDER else 99,
                                            r['serial'], r['file_name']))
    xlsx = _write_excel(all_rows)
    zip_entries.append(("发票汇总.xlsx", xlsx))

    # ---- 航空类 OFD/XML 原件文件夹 (仅在范围内有该类 OFD/XML 原件时产出) ----
    # 命名 "审批编号__原文件名" 防重名; OFD 同时把内嵌 XML (数电票XML版票体) 一并解出放 "-XML.zip",
    # 与列表页单票 "XML" 下载同款结构; 独立 .xml 原件直接放入。
    air_entries: list = []
    if air_aux:
        for it in sorted(air_aux, key=lambda r: (_dk(r), r['serial'], r['file_name'])):
            aux = aux_map.get(it['id'])
            if not aux:
                continue
            base = f"{it['serial']}__{it['file_name']}"
            if it['ext'] == 'ofd':
                air_entries.append((f"{air_folder}/{base}", aux))
                xmls = extract_ofd_xmls(aux)
                if xmls:
                    bbuf = io.BytesIO()
                    with zipfile.ZipFile(bbuf, "w", zipfile.ZIP_DEFLATED) as z:
                        used: set = set()
                        for inner, xb in xmls:
                            p = _safe_name(f"{base}/{Path(inner).name}")
                            if p in used:
                                p = _safe_name(f"{base}/{len(used)}_{Path(inner).name}")
                            used.add(p)
                            z.writestr(p, xb)
                    air_entries.append((f"{air_folder}/{base}-XML.zip", bbuf.getvalue()))
            elif it['ext'] == 'xml':
                air_entries.append((f"{air_folder}/{base}", aux))
        zip_entries.extend(air_entries)

    # ---- 外层 zip (7 个固定文件 + 航空 OFD/XML 原件文件夹) ----
    _set(tid, progress=96, current="打外层包")
    iso_dates = [k for k in {it['date_key'] for it in items} if k != '无日期']
    if len(iso_dates) == 1:
        rng = iso_dates[0]
    elif iso_dates:
        rng = f"{min(iso_dates)}~{max(iso_dates)}"
    else:
        rng = "无日期"
    outer_name = f"{rng}发票导出.zip"
    outer = workdir / outer_name
    with zipfile.ZipFile(outer, "w", zipfile.ZIP_DEFLATED) as z:
        for zpath, data in zip_entries:
            z.writestr(zpath, data)

    # 回写导出时间 + 追加导出历史 (2026-11-24): 范围内全部发票标记 exported_at & export_history
    try:
        record_invoice_export(invoice_ids)
    except Exception as _e:
        with _tasks_lock:
            _tasks[tid]["errors"].append(f"导出时间/历史回写失败: {_e}")

    _set(tid, progress=100, status="done", current="完成",
         filename=outer_name,
         finished_at=datetime.now().isoformat(timespec="seconds"))


# ---------------- 导出时间/历史回写 (2026-11-24): 批量导出 + 单张下载 均记录, 历史全保留 ----------------
def record_invoice_export(invoice_ids: list, ts: str = None) -> None:
    """把本次导出时间追加到相关发票的 export_history (JSON数组, 末位=最近), 并回写 exported_at。

    批量导出与单张下载均调用, 历史时间全部保留 (供前端"导出状态"列 hover 展示全部记录)。
    """
    if not invoice_ids:
        return
    ts = ts or datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    _db = SessionLocal()
    try:
        for _iid in invoice_ids:
            _inv = _db.get(Invoice, _iid)
            if not _inv:
                continue
            _inv._append_export_history(ts)
            _inv.exported_at = datetime.now()
        _db.commit()
    except Exception:
        _db.rollback()
        raise
    finally:
        _db.close()


# ---------------- API 端点 ----------------
from fastapi import APIRouter, HTTPException  # noqa: E402

router = APIRouter(prefix="/api/invoices", tags=["invoice-export"])


@router.post("/export")
async def start_export(payload: dict):
    ids = [int(x) for x in (payload.get("invoice_ids") or []) if str(x).isdigit()][:1000]
    if not ids:
        raise HTTPException(status_code=400, detail="invoice_ids 不能为空")
    tid = _new_task()
    EXPORT_ROOT.mkdir(parents=True, exist_ok=True)
    (EXPORT_ROOT / tid).mkdir(parents=True, exist_ok=True)
    threading.Thread(target=run_export_task, args=(tid, ids), daemon=True).start()
    return {"task_id": tid}


@router.get("/export/{task_id}")
async def export_status(task_id: str):
    t = get_task(task_id)
    if not t:
        raise HTTPException(status_code=404, detail="任务不存在或已过期")
    return t


@router.get("/export/{task_id}/download")
async def export_download(task_id: str):
    t = _task_state(task_id)
    if not t:
        raise HTTPException(status_code=404, detail="任务不存在或已过期")
    if t["status"] != "done":
        raise HTTPException(status_code=409, detail=f"未完成 (status={t['status']})")
    p = Path(t["path"]) / (t.get("filename") or "")
    if not p.is_file():
        raise HTTPException(status_code=410, detail="文件已清理")
    from fastapi.responses import FileResponse
    return FileResponse(p, media_type="application/zip", filename=t.get("filename") or p.name)


# ================= 单张发票 原件/OFD/XML 下载 (2026-11-19: 发票列表行内按钮) =================
# 与批量导出同源: 原件走 fetch_invoice_file (本地缓存优先, miss 时飞书回源);
# XML = 从 OFD(zip) 内嵌提取, 打包为 zip 下发; 原件为 PDF 时无法提供 XML/OFD。

from fastapi.responses import Response  # noqa: E402
from urllib.parse import quote as _quote  # noqa: E402


def _inv_ext(inv):
    return inv.file_name.rsplit(".", 1)[-1].lower() if "." in inv.file_name else ""


def _attachment_disp(name: str) -> dict:
    return {"Content-Disposition": f"attachment; filename*=UTF-8''{_quote(name)}"}


@router.get("/{invoice_id}/formats")
async def invoice_formats(invoice_id: int):
    """该发票原件可用的下载格式: pdf/ofd 看原件扩展名, xml 仅 OFD 原件可提取。"""
    db = SessionLocal()
    try:
        inv = db.get(Invoice, invoice_id)
    finally:
        db.close()
    if not inv:
        raise HTTPException(status_code=404, detail="发票不存在")
    ext = _inv_ext(inv)
    return {"id": inv.id, "file_name": inv.file_name,
            "pdf": ext == "pdf", "ofd": ext == "ofd", "xml": ext == "ofd"}


@router.get("/{invoice_id}/format/{fmt}")
async def invoice_format_file(invoice_id: int, fmt: str):
    """按格式下载单张发票: pdf=原件PDF / ofd=原件OFD / xml=OFD内嵌XML打包zip"""
    fmt = (fmt or "").lower()
    db = SessionLocal()
    try:
        inv = db.get(Invoice, invoice_id)
        ap = db.get(Approval, inv.approval_id) if inv else None
    finally:
        db.close()
    if not inv:
        raise HTTPException(status_code=404, detail="发票不存在")
    ext = _inv_ext(inv)
    stem = inv.file_name.rsplit(".", 1)[0] if "." in inv.file_name else inv.file_name
    try:
        data = fetch_invoice_file(ap.serial_number if ap else "", inv.file_name, inv.approval_id)
    except Exception as e:
        raise HTTPException(status_code=404, detail=f"原件获取失败: {e}")
    # 单张下载也算一次导出 (2026-11-24): 追加导出历史, 前端"导出状态"绿对号即时反映
    try:
        record_invoice_export([invoice_id])
    except Exception:
        pass  # 回写失败不阻断下载本身
    if fmt == "pdf":
        if ext != "pdf":
            raise HTTPException(status_code=404, detail="原件不是PDF格式")
        return Response(content=data, media_type="application/pdf",
                        headers=_attachment_disp(inv.file_name))
    if fmt == "ofd":
        if ext != "ofd":
            raise HTTPException(status_code=404, detail="原件不是OFD格式")
        return Response(content=data, media_type="application/octet-stream",
                        headers=_attachment_disp(inv.file_name))
    if fmt == "xml":
        if ext != "ofd":
            raise HTTPException(status_code=404, detail="原件非OFD格式, 无可提取XML")
        xmls = extract_ofd_xmls(data)
        if not xmls:
            raise HTTPException(status_code=404, detail="OFD内未提取到XML")
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            used: set = set()
            for inner, xb in xmls:
                z.writestr(_safe_name(f"{stem}/{Path(inner).name}"), xb)
        zname = f"{stem}-XML.zip"
        return Response(content=buf.getvalue(), media_type="application/zip",
                        headers=_attachment_disp(zname))
    raise HTTPException(status_code=400, detail=f"不支持的格式: {fmt}")
