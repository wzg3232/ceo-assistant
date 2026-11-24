#!/usr/bin/env python
"""从 其他车票.pdf 提取行程单每笔明细（PyMuPDF find_tables 表格识别），写入 audit_results_v2.json
行程单页: 第6/7/8页 (索引5/6/7)。金额合计与页面声称值交叉校验。
"""
import pymupdf as fitz
import json
import re

PDF_PATH = r"D:\program\feishu-approval-ai\data\temp\instance_A2974A7A\attachment_3.pdf"
JSON_PATH = r"D:\program\ceo-assistant\backend\audit_results_v2.json"
KEY_PREFIX = "202607290005"

def trip_type_name(text):
    if '滴滴' in text:
        return '滴滴出行行程单'
    if '曹操' in text:
        return '曹操出行行程单'
    return '网约车行程单'

def parse_page(page):
    text = page.get_text()
    m = re.search(r'共(\d+)笔行程，\s*合计([\d.]+)元', text)
    claim_count = int(m.group(1)) if m else 0
    claim_total = float(m.group(2)) if m else 0.0
    m2 = re.search(r'行程人手机号：(\d+)', text)
    phone = m2.group(1) if m2 else ''
    tname = trip_type_name(text)

    tabs = page.find_tables()
    details = []
    for t in tabs.tables:
        rows = t.extract()
        if not rows:
            continue
        header = [ (c or '').replace('\n', '') for c in rows[0] ]
        if '序号' not in header:
            continue
        for row in rows[1:]:
            cells = [ (c or '').replace('\n', '') for c in row ]
            if not cells[0].strip().isdigit():
                continue
            def g(name):
                try:
                    return cells[header.index(name)]
                except (ValueError, IndexError):
                    return ''
            details.append({
                'seq': int(cells[0]),
                'type': g('车型'),
                'time': g('上车时间'),
                'city': g('城市'),
                'from': g('起点'),
                'to': g('终点'),
                'distance': g('里程[公里]') or g('里程'),
                'amount': g('金额[元]') or g('金额'),
            })

    total = sum(float(d['amount']) for d in details if d['amount'])
    ok = abs(total - claim_total) < 0.01 and len(details) == claim_count
    print(f"  校验: 提取{len(details)}笔/{total:.2f}元 页面声称{claim_count}笔/{claim_total:.2f}元 → {'✓' if ok else '✗'}")
    return tname, details, claim_count, claim_total, phone, ok

def main():
    doc = fitz.open(PDF_PATH)
    page_details = {}
    all_ok = True
    for pidx in [5, 6, 7]:
        print(f"第{pidx+1}页:")
        tname, details, cc, ct, phone, ok = parse_page(doc[pidx])
        all_ok = all_ok and ok
        page_details[pidx] = {'details': details, 'phone': phone,
                              'trip_type': tname, 'trips': len(details),
                              'total_amount': round(ct, 2)}

    with open(JSON_PATH, 'r', encoding='utf-8') as f:
        data = json.load(f)

    for key, group in data.get('groups', {}).items():
        if key.startswith(KEY_PREFIX):
            for t in group.get('trip_summaries', []):
                pidx = t.get('page', t.get('pages')) - 1  # 1-based → 0-based
                if pidx in page_details:
                    pd = page_details[pidx]
                    if abs(pd['total_amount'] - t.get('total_amount', 0)) > 0.01:
                        print(f"⚠ 第{pidx+1}页金额不匹配，跳过")
                        continue
                    t['details'] = pd['details']
                    t.setdefault('phone', pd['phone'])
                    t['trip_type'] = pd['trip_type']
                    t['trips'] = pd['trips']
                    t['total_amount'] = pd['total_amount']
                    print(f"第{pidx+1}页 → {pd['trips']}笔明细已填入 (¥{pd['total_amount']} 校验通过)")

    with open(JSON_PATH, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"\n{'全部校验通过' if all_ok else '⚠ 存在校验未通过'}，已写入 {JSON_PATH}")

if __name__ == '__main__':
    main()
