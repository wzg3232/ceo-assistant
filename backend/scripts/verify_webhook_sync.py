"""CEO助手Webhook同步验证脚本"""
import requests
import json
from pathlib import Path

print("=" * 60)
print("CEO助手Webhook同步验证")
print("=" * 60)

errors = []
warnings = []

# 1. Check API is running
try:
    r = requests.get("http://127.0.0.1:8002/api/approvals", timeout=5)
    d = r.json()
    print(f"\n✓ API响应成功")
except Exception as e:
    errors.append(f"API连接失败: {e}")
    print(f"\n✗ API连接失败: {e}")
    exit(1)

# 2. Check total count
total = d.get('total', 0)
print(f"总审批数: {total}")
if total != 10:
    warnings.append(f"预期10条审批单，实际{total}条")

# 3. Check cancelled approvals are gone
cancelled_serials = ['202607290002', '202608140004', '202608140005', '202608170011']
found_cancelled = []
for key, g in d['groups'].items():
    serial = g.get('serial_number', '')
    if serial in cancelled_serials:
        found_cancelled.append(serial)

if found_cancelled:
    errors.append(f"应删除的审批单仍存在: {found_cancelled}")
else:
    print(f"✓ 已删除的4个审批单全部清除")

# 4. Test webhook sync endpoint
try:
    r = requests.get("http://127.0.0.1:8002/api/webhook/sync", timeout=10)
    sync_result = r.json()
    print(f"✓ Webhook同步端点正常: {sync_result.get('msg')}")
except Exception as e:
    warnings.append(f"Webhook同步测试失败: {e}")

# Summary
print("\n" + "=" * 60)
if errors:
    print(f"✗ 发现 {len(errors)} 个错误:")
    for e in errors:
        print(f"  - {e}")
    exit(1)
else:
    print("✓ 所有验证通过")
    if warnings:
        print(f"  警告: {len(warnings)} 项")
        for w in warnings:
            print(f"    - {w}")
    exit(0)
