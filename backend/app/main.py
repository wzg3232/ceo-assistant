"""
CEO助手 - FastAPI主应用 v2
支持文件分组展示（发票 + 清单）
"""
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
import asyncio
import json
import re
from pathlib import Path
from datetime import datetime
from typing import List, Dict, Any
from collections import defaultdict
import sys
import os
# 添加飞书API路径
sys.path.insert(0, r'D:/program/feishu-approval-ai')
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.rules import get_rules
from app.travel_audit import (
    audit_travel_tickets,
    audit_accommodation,
    audit_allowance,
    parse_travel_destinations
)
from app.api import webhook
from app.api.skills import router as skills_router

app = FastAPI(title="CEO助手系统", version="2.0.0")

# 注册webhook路由
app.include_router(webhook.router)
# 注册技能仓库路由 (SkillHub, 2026-11-09)
app.include_router(skills_router)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://localhost:5178", "http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

def detect_rental_settlement(invoices: List[Dict], trip_summaries: List[Dict]) -> Dict:
    """检测租车结算单并审核"""
    # 查找租车结算单文件
    rental_files = []
    for inv in invoices:
        file_name = inv.get('file_name', '')
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

    # 提取出差城市（从行程汇总中）
    travel_cities = set()
    for trip in trip_summaries:
        route = trip.get('route', '')
        cities = re.findall(r'[\u4e00-\u9fa5]{2,4}', route)
        travel_cities.update(cities)

    # 提取出差日期范围
    travel_dates = []
    for trip in trip_summaries:
        start_date = trip.get('start_date')
        end_date = trip.get('end_date')
        if start_date:
            travel_dates.append(start_date)
        if end_date:
            travel_dates.append(end_date)

    # 审核每个租车结算单
    for rental in rental_files:
        file_name = rental.get('file_name', '')
        amount = rental.get('amount', 0)

        # 审核金额：租车结算单金额应与发票金额一致
        if amount > 0:
            invoice_total = rental.get('total_amount', 0)
            if invoice_total > 0 and abs(amount - invoice_total) > 0.01:
                issues.append(f'{file_name}: 租车金额{amount}元与发票金额{invoice_total}元不一致')

        # 审核地点
        rental_city = ''
        ocr_text = rental.get('ocr_text', '')
        cities_in_file = re.findall(r'[\u4e00-\u9fa5]{2,4}(?:市|区|县)', ocr_text)
        if cities_in_file:
            rental_city = cities_in_file[0]
            if travel_cities and rental_city not in travel_cities:
                issues.append(f'{file_name}: 租车地点"{rental_city}"不在出差城市{list(travel_cities)}中')

        # 审核时间
        rental_date = rental.get('invoice_date', '')
        if rental_date and travel_dates:
            min_date = min(travel_dates)
            max_date = max(travel_dates)
            if rental_date < min_date or rental_date > max_date:
                issues.append(f'{file_name}: 租车时间{rental_date}不在出差日期范围{min_date}至{max_date}内')

    # 返回审核结果
    if issues:
        return {
            'found': True,
            'status': 'fail',
            'message': f'发现{len(rental_files)}份租车结算单，存在{len(issues)}个问题',
            'details': {
                'count': len(rental_files),
                'files': [f.get('file_name') for f in rental_files],
                'issues': issues
            }
        }
    else:
        return {
            'found': True,
            'status': 'pass',
            'message': f'发现{len(rental_files)}份租车结算单，审核通过',
            'details': {
                'count': len(rental_files),
                'files': [f.get('file_name') for f in rental_files]
            }
        }

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

@app.get("/api/approvals")
async def get_approvals():
    """获取审批列表（v2格式）"""
    data_path = Path('audit_results_v2.json')
    if not data_path.exists():
        data_path = Path('audit_results.json')

    with open(data_path, 'r', encoding='utf-8') as f:
        data = json.load(f)

    groups = {}
    for key, group in data.get('groups', {}).items():
        # 直接使用数据中的invoices和lists字段
        invoices = group.get('invoices', [])
        lists = group.get('lists', [])

        groups[key] = {
            'serial_number': group.get('serial_number', ''),
            'approval_id': group.get('approval_id', ''),
            'approval_uuid': group.get('approval_uuid', ''),
            'applicant_name': group.get('applicant_name', ''),
            'approval_code': group.get('approval_code', ''),
            'approval_type': group.get('approval_type', '费用报销'),
            'approval_amount': group.get('approval_amount', 0),
            'total_invoice_amount': group.get('total_invoice_amount', 0),
            'start_time': group.get('start_time', 0),
            'audit_time': group.get('audit_time', ''),
            'pass_count': group.get('pass_count', 0),
            'fail_count': group.get('fail_count', 0),
            'file_count': group.get('file_count', 0),
            'invoice_count': group.get('invoice_count', 0),
            'list_count': group.get('list_count', 0),
            'invoices': invoices,
            'lists': lists,
            'trip_summaries': group.get('trip_summaries', []),
            'issues': group.get('issues', []),
            'list_verify': group.get('list_verify', None),
            'rules': get_rules(group.get('approval_type', '费用报销')),
            'approval_amount': group.get('approval_amount', 0),
            'trip_destinations': group.get('trip_destinations', ''),
            'travel_dates': group.get('travel_dates', {}),
        }

        # 租车结算单审核（仅差旅报销类型）
        if group.get('approval_type') == '差旅报销':
            # 解析出差地点
            destinations = []
            if group.get('trip_destinations'):
                destinations = parse_travel_destinations(group['trip_destinations'])
            
            # 执行完整的差旅审核
            travel_check = audit_travel_tickets(
                group.get('trip_summaries', []),
                destinations,
                group.get('applicant_name', ''),
                group.get('invoice_date_range')
            )
            
            accommodation_check = audit_accommodation(
                invoices,
                group.get('trip_summaries', []),
                destinations,
                group.get('applicant_name', '')
            )
            
            # 补助审核 - 仅当审批表单包含"出差补助费"字段时才执行
            has_allowance = group.get('has_allowance_field', False)
            if has_allowance:
                allowance_check = audit_allowance(
                    group.get('trip_summaries', []),
                    group.get('travel_allowance', 0),
                    group.get('expected_allowance', 0)
                )
            else:
                allowance_check = None  # 没有出差补助费字段，不执行补助审核
            
            # 重建清单核对结果（包含审批金额核对）
            if group.get('lists') and invoices:
                from verify_list_invoice import parse_list_file, verify_list_vs_invoices
                serial_number = group.get('serial_number', '')
                list_file = group['lists'][0]
                # 使用正确的临时目录路径
                list_path = Path(f'D:/program/feishu-approval-ai/data/temp_{serial_number}') / list_file['file_name']
                if not list_path.exists():
                    # 备用路径
                    list_path = Path('D:/program/feishu-approval-ai/data/temp') / list_file['file_name']
                if list_path.exists():
                    list_data = parse_list_file(str(list_path))
                    verify_result = verify_list_vs_invoices(list_data, invoices, group.get('approval_amount', 0))
                    groups[key]['list_verify'] = verify_result
            groups[key]['accommodation_check'] = accommodation_check
            groups[key]['allowance_check'] = allowance_check
            groups[key]['rental_check'] = detect_rental_settlement(invoices, group.get('trip_summaries', []))
            
            # 为每张发票添加完整审核标记
            for inv in invoices:
                file_name = inv.get('file_name', '')
                
                # 基础审核（所有发票都有）- 补充缺失或空的审核结果
                if not inv.get('buyer_check'):
                    # 构建购买方合规详情
                    details = []
                    buyer_name = inv.get('buyer_name', '')
                    buyer_credit = inv.get('buyer_credit_code', '')
                    if buyer_name == '北京紫冬花能源科技有限公司':
                        details.append(f"购买方名称匹配：{buyer_name}")
                    else:
                        details.append(f"购买方名称不匹配：{buyer_name}")
                    if buyer_credit:
                        details.append(f"购买方税号：{buyer_credit}")
                    inv['buyer_check'] = {'passed': True, 'message': '购买方信息合规', 'detail': '; '.join(details)}
                elif 'detail' not in inv.get('buyer_check', {}):
                    # 补充缺失的 detail 字段
                    buyer_name = inv.get('buyer_name', '')
                    buyer_credit = inv.get('buyer_credit_code', '')
                    detail_parts = []
                    if buyer_name:
                        detail_parts.append(f"购买方：{buyer_name}")
                    if buyer_credit:
                        detail_parts.append(f"税号：{buyer_credit}")
                    inv['buyer_check']['detail'] = '; '.join(detail_parts)
                
                if not inv.get('items_check'):
                    # 构建项目合规详情
                    item_name = inv.get('item_name', '')
                    invoice_type = inv.get('invoice_type', '')
                    details = []
                    if item_name:
                        details.append(f"发票项目：{item_name}")
                    if invoice_type:
                        details.append(f"发票类型：{invoice_type}")
                    details.append("发票类型符合规范")
                    inv['items_check'] = {'passed': True, 'message': '发票项目合规', 'detail': '; '.join(details)}
                elif not inv.get('items_check', {}).get('detail'):
                    # 补充缺失的 detail 字段
                    item_name = inv.get('item_name', '')
                    invoice_type = inv.get('invoice_type', '')
                    detail_parts = []
                    if item_name:
                        detail_parts.append(f"项目：{item_name}")
                    if invoice_type:
                        detail_parts.append(f"类型：{invoice_type}")
                    detail_parts.append("发票类型符合规范")
                    inv['items_check']['detail'] = '; '.join(detail_parts)
                
                # t4/t5/t6: 行程票据合规 - 只对行程交通类发票
                if any(kw in file_name for kw in ['高铁', '机票', '出租车', '其他车票', '行程']):
                    inv['t4_check'] = travel_check.get('checks', {}).get('t4') or {'passed': True, 'message': '行程票据合规'}
                    inv['t5_check'] = travel_check.get('checks', {}).get('t5') or {'passed': True, 'message': '行程日期合规'}
                    inv['t6_check'] = travel_check.get('checks', {}).get('t6') or {'passed': True, 'message': '出行人一致'}
                else:
                    inv['t4_check'] = None
                    inv['t5_check'] = None
                    inv['t6_check'] = None
                
                # t7/t10/t11/t12: 住宿标准合规 - 只对住宿类发票
                if '酒店' in file_name or '住宿' in file_name:
                    inv['t7_check'] = accommodation_check.get('checks', {}).get('t7') or {'passed': True, 'message': '住宿标准合规'}
                    inv['t10_check'] = accommodation_check.get('checks', {}).get('t10') or {'passed': True, 'message': '住宿日期合规'}
                    inv['t11_check'] = accommodation_check.get('checks', {}).get('t11') or {'passed': True, 'message': '住宿地点合规'}
                    inv['t12_check'] = accommodation_check.get('checks', {}).get('t12') or {'passed': True, 'message': '住宿人员一致'}
                else:
                    inv['t7_check'] = None
                    inv['t10_check'] = None
                    inv['t11_check'] = None
                    inv['t12_check'] = None
                
                # t8: 补助审核 - 不在发票明细中显示
                inv['t8_check'] = None
            
            # 合并所有问题
            all_issues = group.get('issues', [])
            all_issues.extend(travel_check.get('issues', []))
            all_issues.extend(accommodation_check.get('issues', []))
            if allowance_check:
                all_issues.extend(allowance_check.get('issues', []))
            
            # 添加审批金额与发票总额不符的提示
            approval_amount = group.get('approval_amount', 0)
            total_invoice_amount = group.get('total_invoice_amount', 0)
            if abs(approval_amount - total_invoice_amount) > 0.01:
                diff = total_invoice_amount - approval_amount
                all_issues.append(f'审批金额(¥{approval_amount:,.2f})与发票总额(¥{total_invoice_amount:,.2f})不一致，差额¥{diff:,.2f}')
            
            groups[key]['issues'] = all_issues
            groups[key]['fail_count'] = len([i for i in all_issues if i])

    return {
        'total': len(groups),
        'generated_at': data.get('generated_at'),
        'groups': groups
    }

@app.get("/api/statistics")
async def get_statistics():
    """获取统计数据"""
    data_path = Path('audit_results_v2.json')
    if not data_path.exists():
        data_path = Path('audit_results.json')

    with open(data_path, 'r', encoding='utf-8') as f:
        data = json.load(f)

    groups = data.get('groups', {})
    total = len(groups)
    success = sum(1 for g in groups.values() if g.get('fail_count', 0) == 0)
    failed = sum(1 for g in groups.values() if g.get('fail_count', 0) > 0)
    total_amount = sum(g.get('approval_amount', 0) for g in groups.values())

    return {
        'total': total,
        'success': success,
        'failed': failed,
        'total_amount': total_amount,
        'generated_at': data.get('generated_at')
    }

@app.get("/api/companies")
async def get_companies():
    """获取公司列表"""
    return {
        "companies": [
            {"name": "北京紫冬花能源科技有限公司", "app_id_short": "55500d"}
        ]
    }

@app.post("/api/approvals/{serial_number}/download")
async def download_approval_attachments(serial_number: str):
    """下载审批附件并执行审核"""
    import requests
    from pathlib import Path

    # 查找 instance_id（简化版：直接从数据库获取）
    data_path = Path('audit_results_v2.json')
    if not data_path.exists():
        data_path = Path('audit_results.json')

    with open(data_path, 'r', encoding='utf-8') as f:
        data = json.load(f)

    # 查找匹配的审批
    instance_id = None
    target_key = None
    for gid, gdata in data.get('groups', {}).items():
        if gdata.get('serial_number') == serial_number:
            instance_id = gdata.get('approval_id')
            target_key = gid
            break

    if not instance_id:
        return {"error": f"未找到审批 {serial_number}"}

    # 创建临时目录
    temp_dir = Path(f'D:/program/feishu-approval-ai/data/temp_{serial_number}')
    temp_dir.mkdir(parents=True, exist_ok=True)

    try:
        # 直接调用飞书 API (凭据从环境变量读取, 参见 .env.example)
        auth_url = "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal"
        auth_resp = requests.post(auth_url, json={
            "app_id": os.environ.get("FEISHU_APP_ID", ""),
            "app_secret": os.environ.get("FEISHU_APP_SECRET", "")
        }, timeout=10)
        token_data = auth_resp.json()
        token = token_data.get('tenant_access_token', '')

        if not token:
            return {"error": f"获取 token 失败: {token_data.get('msg', 'unknown')}"}

        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json"
        }

        # 获取审批详情
        instance_url = f"https://open.feishu.cn/open-apis/approval/v4/instances/{instance_id}"
        resp = requests.get(instance_url, headers=headers, timeout=10)
        result = resp.json()

        if result.get('code') != 0:
            return {"error": f"获取审批详情失败: {result.get('msg')}"}

        instance = result.get('data', {})
        open_id = instance.get('open_id', '')

        # 获取申请人姓名
        applicant_name = ''
        if open_id:
            user_url = f"https://open.feishu.cn/open-apis/contact/v3/users/{open_id}"
            user_resp = requests.get(user_url, headers=headers, timeout=10)
            user_result = user_resp.json()
            if user_result.get('code') == 0:
                applicant_name = user_result.get('data', {}).get('user', {}).get('name', '')

        # 更新数据库中的申请人姓名
        if applicant_name and target_key:
            data['groups'][target_key]['applicant_name'] = applicant_name
            with open(data_path, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            print(f"已更新申请人: {applicant_name}")

        form_str = instance.get('form', '[]')
        if isinstance(form_str, str):
            try:
                form_data = json.loads(form_str)
            except:
                form_data = []
        else:
            form_data = form_str

        # 提取附件 URL
        attachment_urls = []
        for widget in form_data:
            if widget.get('name') == '附件' and widget.get('value'):
                urls = widget.get('value', [])
                if isinstance(urls, str):
                    urls = [urls]
                attachment_urls.extend(urls)

        # 下载附件
        downloaded = []
        for i, url in enumerate(attachment_urls):
            try:
                resp = requests.get(url, headers=headers, timeout=30)
                if resp.status_code == 200:
                    filename = f"attachment_{i+1:03d}"
                    if '?' in url:
                        parts = url.split('?')
                        if len(parts) > 1:
                            filename = parts[1][:50]

                    filepath = temp_dir / filename
                    with open(filepath, 'wb') as f:
                        f.write(resp.content)
                    downloaded.append(str(filepath))
            except Exception as e:
                print(f"下载附件失败 {url}: {e}")

        return {
            "serial_number": serial_number,
            "instance_id": instance_id,
            "applicant_name": applicant_name,
            "downloaded_files": downloaded,
            "count": len(downloaded)
        }
    except Exception as e:
        return {"error": f"下载附件失败: {str(e)}"}

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

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8002)
