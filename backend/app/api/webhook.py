"""
飞书Webhook回调接口 - 处理审批状态变更 (v3 Postgres版)
接收飞书webhook后同步状态并清理已取消的审批
数据源: PostgreSQL ceo_audit (feishu-approval-ai 唯一写方, 本接口仅删除已取消审批)
"""
import json
import shutil
import requests
from datetime import datetime
from fastapi import APIRouter, Request

import os

from app.database import SessionLocal
from app.models import Approval

router = APIRouter(prefix="/api/webhook", tags=["webhook"])

FEISHU_TOKEN_URL = "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal"
# 飞书应用凭据从环境变量读取 (不硬编码, 参见仓库根目录 .env.example)
APP_ID = os.environ.get("FEISHU_APP_ID", "")
APP_SECRET = os.environ.get("FEISHU_APP_SECRET", "")

# feishu-approval-ai 附件临时目录 (清理已取消审批时删除)
FEISHU_TEMP_DIRS = 'D:/program/feishu-approval-ai/data'


def get_feishu_token():
    """获取飞书tenant_access_token"""
    try:
        resp = requests.post(FEISHU_TOKEN_URL, json={
            "app_id": APP_ID,
            "app_secret": APP_SECRET
        }, timeout=10)
        data = resp.json()
        return data.get('tenant_access_token', '')
    except Exception as e:
        print(f"获取飞书token失败: {e}")
        return ''


def get_approval_status(instance_id: str):
    """获取审批实例状态"""
    token = get_feishu_token()
    if not token:
        return None

    url = f"https://open.feishu.cn/open-apis/approval/v4/instances/{instance_id}"
    headers = {"Authorization": f"Bearer {token}"}

    try:
        resp = requests.get(url, headers=headers, timeout=10)
        data = resp.json()
        if data.get('code') == 0:
            return data.get('data', {})
        return None
    except Exception as e:
        print(f"获取审批状态失败: {e}")
        return None


def cleanup_cancelled_approval(instance_id: str, serial_number: str) -> bool:
    """清理已取消的审批单 (Postgres, 级联删除子表)"""
    try:
        db = SessionLocal()
        try:
            # 优先按 serial_number 查, 其次按 approval_id (instance_code) 查
            approval = None
            if serial_number:
                approval = db.query(Approval).filter(
                    Approval.serial_number == serial_number).first()
            if not approval and instance_id:
                approval = db.query(Approval).filter(
                    Approval.approval_id == instance_id).first()
            if not approval:
                print(f"未找到审批记录: {serial_number} / {instance_id}")
                return False

            # 级联删除: invoices / approval_lists(+list_items) / trip_summaries(+trip_details) / issues
            db.delete(approval)
            db.commit()
            print(f"已删除审批数据: {approval.serial_number} (Postgres)")

            # 删除附件临时目录
            if serial_number:
                temp_dir = f'{FEISHU_TEMP_DIRS}/temp_{serial_number}'
                shutil.rmtree(temp_dir, ignore_errors=True)
            if instance_id:
                for d in [f'{FEISHU_TEMP_DIRS}/temp/{instance_id}',
                          f'{FEISHU_TEMP_DIRS}/temp/{instance_id.split("-")[0]}']:
                    shutil.rmtree(d, ignore_errors=True)

            return True
        finally:
            db.close()
    except Exception as e:
        print(f"清理失败: {e}")
        return False


@router.post("/event")
async def handle_feishu_event(request: Request):
    """处理飞书事件回调"""
    try:
        data = await request.json()
        print(f"收到飞书webhook事件: {json.dumps(data, ensure_ascii=False)[:500]}")

        # URL验证
        if data.get('type') == 'url_verification':
            return {"challenge": data.get('challenge')}

        # 事件回调
        header = data.get('header', {})
        event_type = header.get('event_type', '')
        print(f"事件类型: {event_type}")

        if event_type == 'approval.instance:v1.0':
            event = data.get('event', {})
            instance_id = event.get('instance_id', '')
            status = event.get('status', '')
            print(f"审批实例 {instance_id} 状态变更: {status}")

            instance_data = get_approval_status(instance_id)
            if instance_data:
                serial_number = instance_data.get('serial_number', '')
                print(f"流水号: {serial_number}")

                if status == 'CANCELED':
                    print(f"审批 {serial_number} 已取消，清理数据...")
                    success = cleanup_cancelled_approval(instance_id, serial_number)
                    return {
                        "code": 0,
                        "msg": "success" if success else "failed",
                        "data": {
                            "serial_number": serial_number,
                            "status": status,
                            "action": "deleted" if success else "failed"
                        }
                    }
                else:
                    print(f"审批 {serial_number} 状态: {status}，保持现有数据")
                    return {
                        "code": 0,
                        "msg": f"审批 {serial_number} 状态已更新为: {status}",
                        "data": {
                            "serial_number": serial_number,
                            "status": status
                        }
                    }
            else:
                return {"code": 1, "msg": "获取审批详情失败"}

        return {"code": 0, "msg": "event received"}

    except Exception as e:
        print(f"处理webhook事件失败: {e}")
        return {"code": 1, "msg": str(e)}


@router.get("/sync")
async def sync_all_statuses():
    """手动同步所有审批状态 (Postgres版: 遍历 approvals, 查飞书, 删已取消)"""
    try:
        db = SessionLocal()
        try:
            approvals = db.query(Approval).all()
            to_delete = []
            print("\n=== 检查审批状态 (Postgres) ===")
            for ap in approvals:
                serial_number = ap.serial_number
                approval_id = ap.approval_id

                instance_data = get_approval_status(approval_id)
                if instance_data:
                    status = instance_data.get('status', 'UNKNOWN')
                    print(f"{serial_number}: {status}")
                    if status == 'CANCELED':
                        to_delete.append(ap)
                else:
                    print(f"{serial_number}: 获取状态失败")

            deleted_count = 0
            for ap in to_delete:
                print(f"删除: {ap.serial_number}")
                db.delete(ap)
                db.commit()
                # 清理附件目录
                if ap.serial_number:
                    shutil.rmtree(f'{FEISHU_TEMP_DIRS}/temp_{ap.serial_number}', ignore_errors=True)
                deleted_count += 1

            remaining = db.query(Approval).count()
            return {
                "code": 0,
                "msg": f"同步完成，删除了 {deleted_count} 个已取消的审批",
                "total": remaining,
                "deleted": deleted_count
            }
        finally:
            db.close()
    except Exception as e:
        print(f"同步失败: {e}")
        return {"code": 1, "msg": str(e)}
