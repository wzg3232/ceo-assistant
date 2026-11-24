#!/usr/bin/env python3
"""
清单-发票核对脚本（已修复）
解析清单Excel，对比发票数据，生成核对结果
"""
import json
from pathlib import Path
from datetime import datetime
import openpyxl


def safe_float(val):
    """安全转换为浮点数，支持Excel公式"""
    if val is None:
        return 0.0
    if isinstance(val, str):
        if val.startswith('='):
            try:
                return eval(val[1:])
            except:
                return 0.0
        try:
            return float(val)
        except:
            return 0.0
    try:
        return float(val)
    except:
        return 0.0


def parse_list_file(list_path: str) -> dict:
    """解析清单Excel文件（已修复）

    修复内容（2026-09-10）:
    1. 支持日期为None的行（跨天行程）
    2. 处理合计行提取总额
    3. 支持Excel日期序列号格式
    """
    try:
        wb = openpyxl.load_workbook(list_path, data_only=True)
        ws = wb.active

        items = []
        last_date = None

        for row in ws.iter_rows(min_row=4, max_row=20, values_only=True):
            # 更新日期
            if row[0] is not None and row[0] != '合计':
                date_str = str(row[0])
                try:
                    if isinstance(row[0], datetime):
                        last_date = row[0].strftime('%Y-%m-%d')
                    else:
                        last_date = datetime.strptime(date_str, '%Y-%m-%d').strftime('%Y-%m-%d')
                except:
                    continue

            # 处理合计行
            if row[0] == '合计':
                transport_total = safe_float(row[4])
                hotel_total = safe_float(row[5])
                subsidy_total = safe_float(row[6])
                other_transport_total = safe_float(row[7])
                other_total = safe_float(row[8])
                grand_total = safe_float(row[9])

                return {
                    'company': str(ws['B2'].value or ''),
                    'applicant': str(ws['H2'].value or ''),
                    'items': items,
                    'total_amount': grand_total if grand_total > 0 else transport_total + hotel_total + subsidy_total + other_transport_total + other_total,
                    'summary': {
                        'transport': transport_total,
                        'hotel': hotel_total,
                        'subsidy': subsidy_total,
                        'other_transport': other_transport_total,
                        'other': other_total,
                        'grand_total': grand_total
                    }
                }

            if last_date is None:
                continue

            item = {
                'date': last_date,
                'from': str(row[1] or ''),
                'to': str(row[2] or ''),
                'type': str(row[3] or ''),
                'transport': safe_float(row[4]),
                'hotel': safe_float(row[5]),
                'subsidy': safe_float(row[6]),
                'other_transport': safe_float(row[7]),
                'other': safe_float(row[8]),
            }

            item['total'] = sum([
                item['transport'],
                item['hotel'],
                item['subsidy'],
                item['other_transport'],
                item['other']
            ])

            if item['total'] > 0:
                items.append(item)

        return {
            'company': str(ws['B2'].value or ''),
            'applicant': str(ws['H2'].value or ''),
            'items': items,
            'total_amount': sum(item['total'] for item in items)
        }
    except Exception as e:
        return {'error': str(e), 'items': [], 'total_amount': 0}


def verify_list_vs_invoices(list_data: dict, invoices: list, approval_amount: float = 0) -> dict:
    """对比清单和发票，并核对审批金额"""
    result = {
        'list_total': list_data.get('total_amount', 0),
        'invoice_total': sum(inv.get('total_amount', 0) for inv in invoices),
        'list_items_count': len(list_data.get('items', [])),
        'invoice_count': len(invoices),
        'match': False,
        'issues': [],
        'list_items': list_data.get('items', [])
    }

    # 检查清单总额与发票总额
    diff = abs(result['list_total'] - result['invoice_total'])
    if diff > 0.01:
        result['issues'].append({
            'type': 'amount_mismatch',
            'message': f"清单总额¥{result['list_total']:.2f}与发票总额¥{result['invoice_total']:.2f}不一致，差额¥{diff:.2f}"
        })

    # 检查发票总额与审批金额
    if approval_amount > 0:
        amount_diff = abs(result['invoice_total'] - approval_amount)
        if amount_diff > 0.01:
            result['issues'].append({
                'type': 'approval_amount_mismatch',
                'message': f"发票总额¥{result['invoice_total']:.2f}与审批金额¥{approval_amount:.2f}不一致，差额¥{amount_diff:.2f}"
            })
        result['approval_amount'] = approval_amount
        result['approval_match'] = amount_diff <= 0.01

    result['match'] = len(result['issues']) == 0
    return result


def process_approval_with_list(approval_id: str, data_dir: str = 'D:/program/ceo-assistant/backend'):
    """处理单个审批单的清单核对"""
    audit_file = Path(data_dir) / 'audit_results.json'
    with open(audit_file, 'r', encoding='utf-8') as f:
        audit_data = json.load(f)

    list_file = None
    invoices = []

    for r in audit_data['results']:
        if r.get('approval_id') == approval_id:
            file_name = r.get('file_name', '')
            if '清单' in file_name and file_name.endswith('.xlsx'):
                list_file = file_name
            elif file_name.endswith('.pdf'):
                invoices.append(r)

    if not list_file:
        return {'error': '未找到清单文件'}

    list_path = Path('D:/program/feishu-approval-ai/data/temp') / list_file
    if not list_path.exists():
        return {'error': f'清单文件不存在: {list_path}'}

    list_data = parse_list_file(str(list_path))
    if 'error' in list_data:
        return {'error': f'解析清单失败: {list_data["error"]}'}

    result = verify_list_vs_invoices(list_data, invoices)
    result['list_file'] = list_file
    result['list_company'] = list_data.get('company', '')
    result['list_applicant'] = list_data.get('applicant', '')

    return result


if __name__ == '__main__':
    import sys
    if len(sys.argv) > 1:
        approval_id = sys.argv[1]
        result = process_approval_with_list(approval_id)
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        print("用法: python verify_list_invoice.py <approval_id>")
        print("示例: python verify_list_invoice.py A2974A7A-C494-4A55-9DA2-2BF5574E2D11")
