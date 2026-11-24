#!/usr/bin/env python3
"""
审核报告解析器 - 支持文件分组和清单解析
"""
import json
import re
from pathlib import Path
from typing import List, Dict, Any
from collections import defaultdict

def parse_audit_report(report_path: str) -> List[Dict]:
    """
    解析审核报告Excel，按文件分组返回
    """
    import openpyxl
    
    wb = openpyxl.load_workbook(report_path)
    sheet = wb.active
    
    # 按文件分组
    file_groups = defaultdict(list)
    
    for row in sheet.iter_rows(min_row=2, values_only=True):
        if not row or len(row) < 6:
            continue
        
        approval_id, approval_type, check_item, passed, check_result, details, audit_time = row[:7]
        
        if not details or '文件:' not in details:
            continue
        
        # 提取文件名
        file_match = re.search(r'文件:\s*(.+?)(?:,|$)', details)
        if not file_match:
            continue
        
        file_name = file_match.group(1).strip()
        
        file_groups[file_name].append({
            'check_item': check_item,
            'passed': passed == '✓',
            'check_result': check_result,
            'details': details,
            'audit_time': audit_time
        })
    
    return file_groups


def classify_file(file_name: str) -> str:
    """
    分类文件类型
    返回: 'invoice' | 'list' | 'other'
    """
    if file_name.startswith('⭐'):
        return 'list'  # 清单文件
    if file_name.endswith('.pdf') or file_name.endswith('.jpg') or file_name.endswith('.png'):
        return 'invoice'  # 发票文件
    return 'other'


def parse_xlsx_list(xlsx_path: str) -> Dict:
    """
    解析报销清单Excel文件
    返回: {items: [...], total_amount: float, invoice_count: int}
    """
    import openpyxl
    
    wb = openpyxl.load_workbook(xlsx_path)
    sheet = wb.active
    
    items = []
    total_amount = 0.0
    invoice_count = 0
    
    # 假设清单格式：序号 | 项目 | 金额 | 发票号 | ...
    for i, row in enumerate(sheet.iter_rows(min_row=2, values_only=True), 1):
        if not row or len(row) < 3:
            continue
        
        # 尝试提取项目信息
        item = {
            'seq': i,
            'description': str(row[0]) if row[0] else '',
            'amount': 0.0,
            'invoice_number': ''
        }
        
        # 尝试解析金额
        try:
            if len(row) > 1 and row[1]:
                amount_str = str(row[1]).replace('¥', '').replace(',', '').strip()
                item['amount'] = float(amount_str) if amount_str else 0.0
        except:
            pass
        
        # 尝试解析发票号
        if len(row) > 2 and row[2]:
            item['invoice_number'] = str(row[2])
        
        if item['amount'] > 0:
            total_amount += item['amount']
            if item['invoice_number']:
                invoice_count += 1
        
        items.append(item)
    
    return {
        'items': items,
        'total_amount': total_amount,
        'invoice_count': invoice_count,
        'item_count': len(items)
    }


def build_file_structure(report_data: Dict) -> List[Dict]:
    """
    构建文件结构树
    """
    structure = []
    
    # 遍历所有结果记录
    for record in report_data.get('results', []):
        file_name = record.get('file_name', '')
        file_type = classify_file(file_name)
        
        # 获取审核结果
        buyer_check = record.get('buyer_check', {})
        items_check = record.get('items_check', {})
        compliant = record.get('compliant', True)
        
        if file_type == 'list':
            # 清单文件
            structure.append({
                'file_name': file_name,
                'type': 'list',
                'is_invoice': False,
                'summary': {
                    'compliant': compliant,
                    'buyer_check': buyer_check,
                    'items_check': items_check,
                    'message': buyer_check.get('message', '') if not compliant else '报销清单'
                },
                'invoices': [],
                'raw_data': record
            })
        else:
            # 发票文件
            structure.append({
                'file_name': file_name,
                'type': 'invoice',
                'is_invoice': True,
                'summary': {
                    'compliant': compliant,
                    'buyer_check': buyer_check,
                    'items_check': items_check,
                    'message': buyer_check.get('message', '合规') if compliant else buyer_check.get('message', '不合规')
                },
                'invoices': [{
                    'invoice_type': record.get('invoice_type', ''),
                    'invoice_number': record.get('invoice_number', ''),
                    'invoice_date': record.get('invoice_date', ''),
                    'buyer_name': record.get('buyer_name', ''),
                    'total_amount': record.get('total_amount', 0),
                    'status': '合规' if compliant else '不合规'
                }],
                'raw_data': record
            })
    
    return structure


def process_approval_results(data_path: str, report_dir: str = None) -> Dict:
    """
    主处理函数
    """
    # 加载原始数据
    with open(data_path, 'r', encoding='utf-8') as f:
        data = json.load(f)
    
    # 按审批单分组
    approval_groups = defaultdict(lambda: {
        'invoices': [],
        'files': [],
        'total_amount': 0,
        'approval_amount': 0,
        'pass_count': 0,
        'fail_count': 0,
        'issues': []
    })
    
    # 处理每条记录
    for record in data.get('results', []):
        serial_number = record.get('serial_number', '')
        approval_id = record.get('approval_id', '')
        file_name = record.get('file_name', '')
        
        group_key = f"{serial_number}_{approval_id}"
        group = approval_groups[group_key]
        
        # 复制记录
        group_record = record.copy()
        
        # 判断文件类型
        if file_name.startswith('⭐'):
            group_record['file_type'] = 'list'
            group_record['is_invoice'] = False
            group['issues'].append({
                'file': file_name,
                'type': '清单',
                'message': '报销清单，非发票文件'
            })
        else:
            group_record['file_type'] = 'invoice'
            group_record['is_invoice'] = True
        
        group['invoices'].append(group_record)
        
        # 统计
        if group_record.get('compliant', True):
            group['pass_count'] += 1
        else:
            group['fail_count'] += 1
            group['issues'].append({
                'file': file_name,
                'type': '发票',
                'message': group_record.get('buyer_check', {}).get('message', '') or 
                          group_record.get('items_check', {}).get('message', '')
            })
        
        # 金额统计
        if group_record.get('is_invoice', True):
            group['total_amount'] += group_record.get('total_amount', 0) or 0
    
    # 添加元信息
    result = {
        'generated_at': data.get('generated_at'),
        'total_count': len(data.get('results', [])),
        'success_count': data.get('success_count', 0),
        'failed_count': data.get('failed_count', 0),
        'total_amount': data.get('total_amount', 0),
        'compliant_count': data.get('compliant_count', 0),
        'groups': {}
    }
    
    # 构建分组结果
    for group_key, group in approval_groups.items():
        serial_number, approval_id = group_key.rsplit('_', 1)
        
        # 从第一条记录提取审批信息
        first_record = group['invoices'][0] if group['invoices'] else {}
        
        result['groups'][group_key] = {
            'serial_number': serial_number,
            'approval_id': approval_id,
            'applicant_name': first_record.get('applicant_name', ''),
            'approval_code': first_record.get('approval_code', ''),
            'approval_amount': first_record.get('approval_amount', 0),
            'start_time': first_record.get('start_time', 0),
            'approval_uuid': first_record.get('approval_uuid', ''),
            'total_amount': group['total_amount'],
            'pass_count': group['pass_count'],
            'fail_count': group['fail_count'],
            'issues': group['issues'],
            'files': group['invoices']  # 保留所有文件记录
        }
    
    return result


if __name__ == '__main__':
    # 测试
    data_path = 'D:/program/ceo-assistant/backend/audit_results.json'
    result = process_approval_results(data_path)
    
    print(f"总记录数: {result['total_count']}")
    print(f"审批单数: {len(result['groups'])}")
    print(f"\n=== 202607290005 审批详情 ===")
    
    for key, group in result['groups'].items():
        if '202607290005' in key:
            print(f"审批编号: {group['serial_number']}")
            print(f"发起人: {group['applicant_name']}")
            print(f"审批金额: ¥{group['approval_amount']:.2f}")
            print(f"发票总额: ¥{group['total_amount']:.2f}")
            print(f"通过: {group['pass_count']}, 问题: {group['fail_count']}")
            print(f"\n文件列表:")
            for f in group['files']:
                print(f"  - {f.get('file_name', '')} [{f.get('file_type', 'unknown')}]")
                if not f.get('compliant', True):
                    print(f"    问题: {f.get('buyer_check', {}).get('message', '') or f.get('items_check', {}).get('message', '')}")
            print(f"\n问题项:")
            for issue in group['issues']:
                print(f"  - [{issue['type']}] {issue['file']}: {issue['message']}")
            break
