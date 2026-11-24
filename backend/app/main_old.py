"""
CEO助手 - FastAPI主应用
"""
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
import asyncio
import json
from pathlib import Path
from datetime import datetime
from typing import List, Dict, Any
from collections import defaultdict

# 创建FastAPI应用
app = FastAPI(
    title="CEO助手系统",
    description="企业CEO智能分身系统",
    version="1.0.0"
)

# 配置CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# WebSocket连接管理
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

# 数据模型
class ApprovalGroup:
    def __init__(self):
        self.invoices: List[Dict] = []
        self.total_amount: float = 0
        self.approval_amount: float = 0
        self.pass_count: int = 0
        self.fail_count: int = 0
        self.serial_number: str = ""
        self.applicant_name: str = ""
        self.approver_name: str = "段宇贤"
        self.approval_code: str = ""
        self.approval_type: str = "费用报销"
        self.start_time: int = None
        self.audit_time: str = ""

# 审批类型映射
APPROVAL_TYPE_MAP = {
    'F3972F0E-9A56-46C5-9699-97892DDDE39C': '费用报销',
    'F653817D-94EA-4072-B929-E9A44EA85FC1': '差旅费报销',
}

def load_results() -> Dict[str, Any]:
    """加载审核结果"""
    results_file = Path('audit_results.json')
    if not results_file.exists():
        return {"results": []}
    
    with open(results_file, 'r', encoding='utf-8') as f:
        return json.load(f)

def group_by_approval(results: List[Dict]) -> Dict[str, Dict]:
    """按审批单分组"""
    groups = {}
    
    for r in results:
        aid = r.get('approval_id', 'unknown')
        if aid not in groups:
            groups[aid] = {
                'invoices': [],
                'total_amount': 0,
                'approval_amount': 0,
                'pass_count': 0,
                'fail_count': 0,
                'serial_number': '',
                'applicant_name': '',
                'approver_name': '段宇贤',
                'approval_code': '',
                'approval_type': '费用报销',
                'start_time': None,
                'audit_time': '',
                'approval_id': aid,
                'approval_uuid': ''
            }
        
        group = groups[aid]
        group['invoices'].append(r)
        
        # 提取审批信息
        if r.get('serial_number') and not group['serial_number']:
            group['serial_number'] = r['serial_number']
        if r.get('applicant_name') and not group['applicant_name']:
            group['applicant_name'] = r['applicant_name']
        if r.get('approval_code') and not group['approval_code']:
            group['approval_code'] = r['approval_code']
            group['approval_type'] = APPROVAL_TYPE_MAP.get(r['approval_code'], '费用报销')
        if r.get('start_time') and not group['start_time']:
            group['start_time'] = r['start_time']
        
        # 提取审批金额
        if r.get('approval_amount') and not group['approval_amount']:
            group['approval_amount'] = r['approval_amount']
        
        # 提取uuid（用于生成链接）
        if r.get('approval_uuid') and not group.get('approval_uuid'):
            group['approval_uuid'] = r['approval_uuid']
        
        # 从文件名提取审核时间
        file_name = r.get('file_name', '')
        if file_name.startswith('审核报告_') and not group['audit_time']:
            import re
            match = re.search(r'_(\d{8})_(\d{6})\.xlsx', file_name)
            if match:
                date_str = match.group(1)
                time_str = match.group(2)
                group['audit_time'] = f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:8]} {time_str[:2]}:{time_str[2:4]}:{time_str[4:6]}"
        
        # 统计
        if r.get('is_invoice', True):
            group['total_amount'] += r.get('total_amount', 0) or 0
        
        if r.get('compliant', True):
            group['pass_count'] += 1
        else:
            group['fail_count'] += 1
    
    return groups

# API路由
@app.get("/api/approvals")
async def get_approvals():
    """获取审批列表"""
    data = load_results()
    groups = group_by_approval(data.get('results', []))
    
    return {
        "total": len(groups),
        "generated_at": data.get('generated_at'),
        "groups": groups
    }

@app.get("/api/statistics")
async def get_statistics():
    """获取统计数据"""
    data = load_results()
    groups = group_by_approval(data.get('results', []))
    
    total = len(groups)
    success = sum(1 for g in groups.values() if g['fail_count'] == 0)
    failed = sum(1 for g in groups.values() if g['fail_count'] > 0)
    total_amount = sum(g['total_amount'] for g in groups.values())
    
    return {
        "total": total,
        "success": success,
        "failed": failed,
        "total_amount": total_amount,
        "generated_at": data.get('generated_at')
    }

@app.get("/api/companies")
async def get_companies():
    """获取公司列表"""
    return {
        "companies": [
            {
                "id": 1,
                "name": "北京紫冬花能源科技有限公司",
                "app_id_short": "55500d"
            }
        ]
    }

# WebSocket端点
@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await manager.connect(websocket)
    try:
        while True:
            data = await websocket.receive_text()
            # 可以处理客户端消息
    except WebSocketDisconnect:
        manager.disconnect(websocket)

# 文件监控任务
async def watch_file_changes():
    """监控文件变化"""
    last_modified = 0
    results_file = Path('audit_results.json')
    
    while True:
        try:
            if results_file.exists():
                current_modified = results_file.stat().st_mtime
                
                if current_modified != last_modified:
                    last_modified = current_modified
                    data = load_results()
                    await manager.broadcast({
                        "type": "data_update",
                        "data": data
                    })
            
            await asyncio.sleep(2)
        except Exception as e:
            print(f"文件监控异常: {e}")
            await asyncio.sleep(5)

@app.on_event("startup")
async def startup_event():
    """启动时开始文件监控"""
    asyncio.create_task(watch_file_changes())

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8002)
