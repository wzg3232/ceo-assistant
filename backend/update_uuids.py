"""获取审批实例的uuid并更新数据"""
import requests
import json
from pathlib import Path
from dotenv import load_dotenv
import os

load_dotenv('D:/program/feishu-approval-ai/.env')

APP_ID = os.getenv('FEISHU_APP_ID', '')
APP_SECRET = os.getenv('FEISHU_APP_SECRET', '')

def get_token():
    r = requests.post(
        'https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal',
        json={'app_id': APP_ID, 'app_secret': APP_SECRET}
    )
    return r.json().get('tenant_access_token')

def get_approval_uuid(instance_id: str, token: str) -> str:
    r = requests.get(
        f'https://open.feishu.cn/open-apis/approval/v4/instances/{instance_id}',
        headers={'Authorization': f'Bearer {token}'}
    )
    if r.json().get('code') == 0:
        return r.json()['data'].get('uuid', '')
    return ''

def main():
    token = get_token()
    if not token:
        print("获取token失败")
        return
    
    data_file = Path('D:/program/ceo-assistant/backend/audit_results.json')
    with open(data_file, 'r', encoding='utf-8') as f:
        data = json.load(f)
    
    # 获取所有唯一的approval_id
    approval_ids = set(r.get('approval_id') for r in data['results'] if r.get('approval_id'))
    print(f"需要获取 {len(approval_ids)} 个审批的uuid")
    
    # 建立approval_id到uuid的映射
    uuid_map = {}
    for aid in approval_ids:
        uuid = get_approval_uuid(aid, token)
        if uuid:
            uuid_map[aid] = uuid
            print(f"  {aid} -> {uuid}")
        else:
            print(f"  {aid} -> 获取失败")
    
    # 更新数据文件，为每个result添加uuid
    updated_count = 0
    for r in data['results']:
        aid = r.get('approval_id')
        if aid and aid in uuid_map:
            r['approval_uuid'] = uuid_map[aid]
            updated_count += 1
    
    # 保存
    with open(data_file, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    
    print(f"\n已更新 {updated_count} 条记录")
    print(f"uuid映射: {json.dumps(uuid_map, ensure_ascii=False, indent=2)}")

if __name__ == '__main__':
    main()
