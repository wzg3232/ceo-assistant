"""
租车结算单审核逻辑
"""
import re
from typing import Dict, List, Optional


def _norm_date(s) -> str:
    """日期归一: '2026年08月25日'/'2026-08-25'/'20260825' -> 'YYYY-MM-DD' (不可解析返回'')"""
    m = re.match(r'(\d{4})\D*(\d{1,2})\D*(\d{1,2})', str(s or '').strip())
    if not m:
        return ''
    return f'{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}'


def _is_pure_payment_record(inv: Dict) -> bool:
    """2026-10-19 口径: 纯支付记录/付款截图无地点/行程信息, 不做行程核对。
    判定: 文件名带 支付/付款 记录/截图/凭证 字样。
    (兼容读端 to_dict: 有 total_amount/invoice_date, 无 amount/ocr_text)"""
    file_name = str(inv.get('file_name') or '')
    if re.search(r'支付记录|付款截图|支付截图|付款记录|支付凭证|付款凭证|付款单', file_name, re.IGNORECASE):
        return True
    return False


def _inv_amount(inv: Dict) -> float:
    """金额兼容: 读端 to_dict 用 total_amount, 写端/旧数据可能用 amount"""
    for k in ('total_amount', 'amount'):
        v = inv.get(k)
        if v not in (None, ''):
            try:
                return float(v)
            except (TypeError, ValueError):
                continue
    return 0.0


def detect_rental_settlement(invoices: List[Dict], trip_summaries: List[Dict],
                             list_items: Optional[List[Dict]] = None) -> Dict:
    """
    检测租车结算单并审核

    审核规则：
    1. 金额：租车结算单金额应与发票金额一致
    2. 地点：租车地点应是出差地所在城市
    3. 时间：租车时间应在出差日期范围内
       (口径 2026-10-12 用户确认: 消费日优先, 开票日兜底。
        清单备注中"租车"消费日在出差期间时, 超范围开票日判"事后补开票",
        降级为 warning 供人工核对, 不再判 fail)

    Args:
        invoices: 发票列表
        trip_summaries: 行程汇总列表
        list_items: 报销清单明细 (消费日佐证, 含 date/remark), 可选

    Returns:
        审核结果字典 (details.warnings 为不阻断的提示列表)
    """
    list_items = list_items or []
    # 查找租车结算单文件
    rental_files = []
    for inv in invoices:
        file_name = inv.get('file_name', '')
        # 匹配租车结算单相关文件
        if re.search(r'租车|结算单|租车结算', file_name, re.IGNORECASE):
            rental_files.append(inv)

    # 如果没有找到租车结算单
    if not rental_files:
        return {
            'found': False,
            'status': 'pass',
            'message': '未识别到租车结算单',
            'details': None
        }

    # 找到租车结算单，进行审核
    issues = []
    warnings = []

    # 提取出差城市（从行程汇总中）
    travel_cities = set()
    for trip in trip_summaries:
        route = trip.get('route', '')
        # 从路线中提取城市（如"北京-上海"）
        cities = re.findall(r'[\u4e00-\u9fa5]{2,4}', route)
        travel_cities.update(cities)

    # 提取出差日期范围（归一化为 YYYY-MM-DD, 保证跨格式可比）
    travel_dates = []
    for trip in trip_summaries:
        for key in ('start_date', 'end_date'):
            nd = _norm_date(trip.get(key))
            if nd:
                travel_dates.append(nd)

    # 消费日佐证: 清单中备注含"租车"的行其消费日期 (如 08-20 备注"租车费用145.46元")
    consumption_dates = sorted({
        _norm_date(li.get('date'))
        for li in list_items
        if '租车' in str(li.get('remark') or '') and _norm_date(li.get('date'))
    })

    # 2026-10-19 口径: 区分租车结算单/发票(需行程+地点+日期核对) 与 纯支付记录(无地点信息, 仅金额核对)
    payment_records = [inv for inv in rental_files if _is_pure_payment_record(inv)]
    settlement_files = [inv for inv in rental_files if inv not in payment_records]

    # 租车发票总额(金额基准): 租车类正式发票, 如 145.46+租车发票.pdf
    car_invoice_amount = 0.0
    for inv in invoices:
        if (inv.get('file_type') == 'invoice') and re.search(r'租车', str(inv.get('file_name') or ''), re.IGNORECASE):
            car_invoice_amount += _inv_amount(inv)

    # 纯支付记录: 只做金额核对(与租车发票比), 不套行程/地点/日期核对
    # 口径 2026-10-19: 发票<付款(员工少报)按发票金额测算; 发票>付款按付款金额测算; 均属正常现象
    for pay in payment_records:
        p_file_name = pay.get('file_name', '')
        p_amount = _inv_amount(pay)
        if car_invoice_amount > 0 and abs(p_amount - car_invoice_amount) >= 0.01:
            if p_amount > car_invoice_amount:
                # 付款>发票: 员工少报(多付少票), 按发票金额测算
                warnings.append(
                    f'{p_file_name}: 租车支付金额{p_amount:.2f}元>租车发票{car_invoice_amount:.2f}元, '
                    f'差额{p_amount - car_invoice_amount:.2f}元属员工少报(正常现象), 按发票金额测算'
                )
            else:
                # 付款<发票: 发票大于付款, 按付款金额测算
                warnings.append(
                    f'{p_file_name}: 租车支付金额{p_amount:.2f}元<租车发票{car_invoice_amount:.2f}元, '
                    f'按付款金额测算(正常现象)'
                )

    # 审核每个租车结算单/发票(地点+日期)
    for rental in settlement_files:
        file_name = rental.get('file_name', '')
        amount = rental.get('amount', 0)

        # 1. 审核金额（这里需要与发票金额对比，暂时跳过）
        # TODO: 需要关联对应的发票

        # 2. 审核地点
        rental_city = ''
        # 从文件名或OCR结果中提取城市
        ocr_text = rental.get('ocr_text', '')
        cities_in_file = re.findall(r'[\u4e00-\u9fa5]{2,4}(?:市|区|县)', ocr_text)
        if cities_in_file:
            rental_city = cities_in_file[0]
            if travel_cities and rental_city not in travel_cities:
                issues.append(f'{file_name}: 租车地点"{rental_city}"不在出差城市{list(travel_cities)}中')

        # 3. 审核时间（消费日优先, 开票日兜底）
        rental_date_raw = str(rental.get('invoice_date') or '')
        rental_date = _norm_date(rental_date_raw)
        if rental_date and travel_dates:
            min_date = min(travel_dates)
            max_date = max(travel_dates)
            if rental_date < min_date or rental_date > max_date:
                if consumption_dates and all(min_date <= c <= max_date for c in consumption_dates):
                    # 消费日在出差期间 -> 事后补开票, 降级 warning 供人工核对
                    warnings.append(
                        f'{file_name}: 租车开票日期{rental_date}不在出差日期范围{min_date}至{max_date}内, '
                        f'但清单备注"租车"消费日{"/".join(consumption_dates)}在出差期间, '
                        f'判定为事后补开票(建议人工核对)'
                    )
                else:
                    issues.append(f'{file_name}: 租车时间{rental_date_raw or rental_date}不在出差日期范围{min_date}至{max_date}内')

    # 返回审核结果 (2026-10-19: 计数区分 租车发票/结算单 与 纯支付记录)
    _all = len(rental_files)
    _settle = len(settlement_files)
    _pay = len(payment_records)
    _desc = []
    if _settle:
        _desc.append(f'{_settle}份租车发票/结算单')
    if _pay:
        _desc.append(f'{_pay}份租车支付记录')
    _summary = '、'.join(_desc) if _desc else '0份'

    if issues:
        return {
            'found': True,
            'status': 'fail',
            'message': f'发现{_all}份租车相关单据({_summary})，存在{len(issues)}个问题',
            'details': {
                'count': _all,
                'files': [f.get('file_name') for f in rental_files],
                'issues': issues,
                'warnings': warnings
            }
        }
    else:
        message = f'发现{_all}份租车相关单据({_summary})'
        if warnings:
            message += f'，{len(warnings)}项提示待人工核对'
        else:
            message += '，审核通过'
        return {
            'found': True,
            'status': 'pass',
            'message': message,
            'details': {
                'count': _all,
                'files': [f.get('file_name') for f in rental_files],
                'issues': issues,
                'warnings': warnings
            }
        }
