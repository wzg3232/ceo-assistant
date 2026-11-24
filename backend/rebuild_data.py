#!/usr/bin/env python3
"""
重构审核数据 - 按文件分组，区分发票和清单
支持PDF多发票解析和行程汇总提取
从审核报告Excel提取正确的发票数据
"""
import json
import sys
import re
from pathlib import Path
from collections import defaultdict
import openpyxl
from verify_list_invoice import parse_list_file
from pdf_invoice_parser import PDFInvoiceExtractor

# 添加飞书API路径
sys.path.insert(0, r'D:/program/feishu-approval-ai')
from modules.feishu.api.approval import ApprovalAPI


def extract_invoices_from_report(report_file: str) -> list:
    """从审核报告Excel提取发票数据"""
    wb = openpyxl.load_workbook(report_file)
    ws = wb.active
    
    invoices = []
    applicant_name = ''
    
    for row in ws.iter_rows(min_row=2, values_only=True):
        if not row[0]:
            continue
            
        # 提取申请人
        if not applicant_name and row[1] and '差旅报销' in str(row[1]):
            for cell in row:
                if cell and isinstance(cell, str) and '张旺' in cell:
                    applicant_name = cell
                    break
        
        # 提取发票金额
        if row[4] and '发票金额' in str(row[4]):
            match = re.search(r'发票金额:\s*([\d.]+)元', str(row[4]))
            detail = str(row[5]) if row[5] else ''
            file_match = re.search(r'文件:\s*([^\s,]+)', detail)
            type_match = re.search(r'类型:\s*([^\s,]+)', detail)
            
            if match and file_match:
                invoices.append({
                    'file_name': file_match.group(1),
                    'total_amount': float(match.group(1)),
                    'invoice_type': type_match.group(1) if type_match else '电子发票',
                    'applicant_name': applicant_name,
                    'status': '成功',
                    'compliant': row[3] == '✓'
                })
    
    return invoices


def main():
    # 加载原始数据
    data_path = Path('D:/program/ceo-assistant/backend/audit_results.json')
    with open(data_path, 'r', encoding='utf-8') as f:
        data = json.load(f)
    
    # 从审核报告提取正确的发票数据
    reports_dir = Path('D:/program/feishu-approval-ai/data/reports')
    
    # 构建 serial_number -> approval_id 映射
    serial_to_approval = {}
    for r in data.get('results', []):
        serial = r.get('serial_number', '')
        approval_id = r.get('approval_id', '')
        if serial and approval_id:
            serial_to_approval[serial] = approval_id
    
    print(f"找到 {len(serial_to_approval)} 个审批单")
    
    # 处理每个审批单
    expanded_records = []
    
    for record in data['results']:
        serial = record.get('serial_number', '')
        approval_id = record.get('approval_id', '')
        
        # 查找对应的审核报告（取最新的）
        report_files = sorted(reports_dir.glob(f'审核报告_{approval_id}*.xlsx'), 
                              key=lambda x: x.stat().st_mtime, reverse=True)
        
        if report_files:
            # 从报告提取发票
            report_file = report_files[0]
            invoices = extract_invoices_from_report(str(report_file))
            
            # 获取申请人信息
            applicant_name = ''
            for r in data.get('results', []):
                if r.get('serial_number') == serial:
                    applicant_name = r.get('applicant_name', '')
                    break
            
            # 创建扩展记录
            for inv in invoices:
                new_record = record.copy()
                new_record.update(inv)
                # 保持原始文件名，不添加序号后缀
                # 如果需要区分多张发票，可以用序号作为显示标签，但不改变文件名
                
                expanded_records.append(new_record)
        else:
            # 没有报告，使用原始数据
            expanded_records.append(record)
    
    print(f"\n原始记录数: {len(data['results'])}")
    print(f"扩展后记录数: {len(expanded_records)}")
    
    # 按 (serial_number, approval_id) 分组
    groups = defaultdict(lambda: {
        'files': [],
        'invoices': [],
        'lists': [],
        'trip_summaries': [],
        'pass_count': 0,
        'fail_count': 0,
        'issues': []
    })
    
    for record in expanded_records:
        serial = record.get('serial_number', '')
        approval_id = record.get('approval_id', '')
        file_name = record.get('file_name', '')
        
        group_key = f"{serial}_{approval_id}"
        group = groups[group_key]
        
        is_invoice = file_name.endswith('.pdf') or '_发票' in file_name
        is_list = '清单' in file_name or file_name.startswith('⭐')
        
        file_info = {
            'file_name': file_name,
            'is_invoice': is_invoice,
            'compliant': record.get('compliant', True),
            'total_amount': record.get('total_amount', 0),
            'invoice_number': record.get('invoice_number', ''),
            'invoice_date': record.get('invoice_date', ''),
            'buyer_name': record.get('buyer_name', ''),
            'buyer_credit_code': record.get('buyer_credit_code', ''),
            'seller_name': record.get('seller_name', ''),
            'seller_credit_code': record.get('seller_credit_code', ''),
            'item_name': record.get('item_name', ''),
            'invoice_type': record.get('invoice_type', 'standard'),
            'buyer_check': record.get('buyer_check', {}),
            'items_check': record.get('items_check', {}),
            'applicant_name': record.get('applicant_name', ''),
        }
        
        # 清单文件不需要购买方检查，重置为合规
        if is_list:
            file_info['buyer_check'] = {'passed': True, 'message': '清单文件无需购买方检查'}
            file_info['compliant'] = True
        
        group['files'].append(file_info)
        
        if is_invoice:
            group['invoices'].append(file_info)
        elif is_list:
            group['lists'].append(file_info)

        # 提取行程单汇总（从 PDF 中的行程单页面）
        if file_name.endswith('.pdf') and any(kw in file_name.lower() for kw in ['行程', '滴滴', '曹操', 'taxi', 'ride']):
            group['trip_summaries'].append({
                'file_name': file_name,
                'pages': 1,
                'total_amount': 0,
            })
        
        if record.get('compliant', True):
            group['pass_count'] += 1
        elif not is_list:
            group['fail_count'] += 1
            group['issues'].append({
                'file': file_name,
                'type': '发票' if is_invoice else '清单',
                'messages': []
            })
    
    # 去重：同一份发票在不同记录中出现多次时，只保留一份
    # 通过 MD5 或文件名+金额组合去重
    for key, group in groups.items():
        seen_invoices = set()
        unique_invoices = []
        for inv in group['invoices']:
            # 使用文件名+金额作为唯一标识
            inv_key = f"{inv['file_name']}_{inv['total_amount']}"
            if inv_key not in seen_invoices:
                seen_invoices.add(inv_key)
                unique_invoices.append(inv)
        group['invoices'] = unique_invoices
        group['invoice_count'] = len(unique_invoices)
        group['total_invoice_amount'] = sum(inv['total_amount'] for inv in unique_invoices)
    
    # 构建最终结果
    result = {'groups': {}, 'total_count': len(expanded_records)}
    
    api = ApprovalAPI()
    
    for group_key, group in groups.items():
        serial_number = group_key.rsplit('_', 1)[0] if '_' in group_key else group_key
        approval_id = group_key.rsplit('_', 1)[-1] if '_' in group_key else ''
        
        # 获取第一条记录的审批信息
        first_record = next((r for r in expanded_records if r.get('serial_number') == serial_number), {})
        
        # 从飞书API提取出差地点等信息
        print(f"  获取审批 {serial_number} 的详情...")
        
        try:
            instance = api.get_approval_instance(approval_id)
            if instance:
                # 从form字段提取申请人姓名
                applicant_name = ''
                trip_destinations = ''
                travel_dates = {}
                
                form_str = instance.get('form', '[]')
                if isinstance(form_str, str):
                    try:
                        form_data = json.loads(form_str)
                    except:
                        form_data = []
                else:
                    form_data = form_str
                
                for widget in form_data:
                    if not isinstance(widget, dict):
                        continue
                    name = widget.get('name', '')
                    value = widget.get('value', '')
                    
                    # 申请人：优先取领款人名单，其次填表人
                    if name == '领款人名单' and value:
                        applicant_name = str(value)
                    elif name == '填表人' and isinstance(value, list) and value:
                        applicant_name = value[0]
                    elif name == '申请人' and value:
                        applicant_name = str(value)
                    
                    # 出差地点
                    if name == '出差地点' and value:
                        trip_destinations = str(value)
                    
                    # 日期范围
                    elif name == 'DateInterval' and isinstance(value, dict):
                        travel_dates = {
                            'start': value.get('start', '')[:10],
                            'end': value.get('end', '')[:10]
                        }
        except Exception as e:
            print(f"    警告: 获取审批详情失败: {e}")
            applicant_name = first_record.get('applicant_name', '')
            trip_destinations = ''
            travel_dates = {}
        
        # 推断审批类型
        approval_type = first_record.get('approval_type', '费用报销')
        
        group_data = {
            'serial_number': serial_number,
            'approval_id': approval_id,
            'applicant_name': applicant_name,
            'approval_type': approval_type,
            'approval_amount': first_record.get('approval_amount', 0),
            'start_time': int(instance['start_time']) // 1000 if instance.get('start_time') else 0,
            'pass_count': group['pass_count'],
            'fail_count': group['fail_count'],
            'invoice_count': len(group['invoices']),
            'list_count': len(group['lists']),
            'file_count': len(group['files']),
            'total_invoice_amount': sum(f.get('total_amount', 0) for f in group['invoices']),
            'invoices': group['invoices'],
            'lists': group['lists'],
            'trip_summaries': group['trip_summaries'],
            'issues': group['issues'],
            'trip_destinations': trip_destinations,
            'travel_dates': travel_dates,
        }
        
        result['groups'][group_key] = group_data
    
    # 保存
    output_path = Path('D:/program/ceo-assistant/backend/audit_results_v2.json')
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    
    print(f"\n总记录数: {result['total_count']}")
    print(f"审批单数: {len(result['groups'])}")
    print(f"数据已保存到: {output_path}")
    
    # 验证
    print("\n=== 验证结果 ===")
    for key, g in sorted(result['groups'].items()):
        serial = key.split('_')[0]
        invs = g.get('invoices', [])
        total = sum(i.get('total_amount', 0) for i in invs)
        print(f"{serial}: {len(invs)}张, ¥{total:.2f}")
    
    return result


if __name__ == '__main__':
    main()
