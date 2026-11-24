"""更新审计结果数据 - 从飞书API获取审批金额"""
import requests
import json
from pathlib import Path
from dotenv import load_dotenv
import os

load_dotenv('D:/program/feishu-approval-ai/.env')

APP_ID = os.getenv('FEISHU_APP_ID', '')
APP_SECRET = os.getenv('FEISHU_APP_SECRET', '')

# 获取tenant_access_token
def get_token():
    r = requests.post(
        'https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal',
        json={'app_id': APP_ID, 'app_secret': APP_SECRET}
    )
    return r.json().get('tenant_access_token')

# 获取审批实例详情
def get_approval_instance(instance_id: str, token: str):
    r = requests.get(
        f'https://open.feishu.cn/open-apis/approval/v4/instances/{instance_id}',
        headers={'Authorization': f'Bearer {token}'}
    )
    return r.json()

# 从表单中提取金额
def extract_amount(form_json: str) -> float:
    try:
        form_data = json.loads(form_json)
        for widget in form_data:
            if isinstance(widget, dict) and widget.get('type') == 'amount':
                return float(widget.get('value', 0) or 0)
    except:
        pass
    return 0.0

def main():
    token = get_token()
    if not token:
        print("获取token失败")
        return
    
    # 读取现有数据
    data_file = Path('D:/program/ceo-assistant/backend/audit_results.json')
    with open(data_file, 'r', encoding='utf-8') as f:
        data = json.load(f)
    
    # 获取所有唯一的approval_id
    approval_ids = set(r.get('approval_id') for r in data['results'])
    print(f"需要更新 {len(approval_ids)} 个审批的金额")
    
    # 为每个审批获取金额
    approval_amounts = {}
    for aid in approval_ids:
        if not aid or aid == 'unknown':
            continue
        result = get_approval_instance(aid, token)
        if result.get('code') == 0:
            form = result['data'].get('form', '[]')
            amount = extract_amount(form)
            approval_amounts[aid] = amount
            print(f"  {aid}: ¥{amount:.2f}")
        else:
            print(f"  {aid}: 获取失败 - {result.get('msg')}")
    
    # 更新数据
    updated_count = 0
    for r in data['results']:
        aid = r.get('approval_id')
        if aid and aid in approval_amounts:
            r['approval_amount'] = approval_amounts[aid]
            updated_count += 1
    
    # 保存
    with open(data_file, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    
    print(f"\n已更新 {updated_count} 条记录")
    print(f"金额统计: {sum(approval_amounts.values()):.2f} 元")

if __name__ == '__main__':
    main()
