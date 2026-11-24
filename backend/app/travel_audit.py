"""
差旅报销审核规则 - 行程单、住宿、补助审核
"""
from typing import List, Dict, Any, Optional
import re
from datetime import datetime

# 住宿标准（元/人/天）- 员工按城市档位；领导实报实销不审核
CITY_LEVEL_STANDARDS = {
    # 一线城市（北京、上海、广州、深圳等）
    "北京": 400, "上海": 400, "广州": 400, "深圳": 400,
    # 新一线城市（成都、重庆、杭州、西安、武汉、苏州、郑州、南京、天津、长沙）
    "成都": 350, "重庆": 350, "杭州": 350, "西安": 350, "武汉": 350,
    "苏州": 350, "郑州": 350, "南京": 350, "天津": 350, "长沙": 350,
    # 二线城市（昆明、沈阳、济南、无锡、厦门、福州、南宁、合肥、南昌、温州、常州、中山）
    "昆明": 280, "沈阳": 280, "济南": 280, "无锡": 280, "厦门": 280,
    "福州": 280, "南宁": 280, "合肥": 280, "南昌": 280, "温州": 280,
    "常州": 280, "中山": 280,
    # 三四线城市（默认档）
    "default": 180
}

# 领导名单：实报实销，不做住宿标准审核
# 与 D:\program\feishu-approval-ai\checker\expense_list_checker.py 的 LEADER_NAMES 保持同步
LEADER_NAMES = {'杜瑛卓', '黄更新', '张化为', '张旺', '仲立群', '王剑', '张俊杰'}

def parse_travel_destinations(dest_str: str) -> List[str]:
    """解析出差地点字符串，返回城市列表"""
    if not dest_str:
        return []
    # 按逗号、顿号、空格分割
    cities = re.split(r'[，,、\s]+', dest_str.strip())
    # 过滤空字符串
    return [c.strip() for c in cities if c.strip()]

def extract_cities_from_text(text: str) -> List[str]:
    """从文本中提取城市名"""
    if not text:
        return []
    
    cities = []
    
    # 1. 排除银行名称中的城市（避免误判）
    # 银行名称模式：XX银行股份有限公司、XX银行
    bank_pattern = r'([^\\s]{2,4}银行[^\\s，,、;；:]*)'
    text_no_bank = re.sub(bank_pattern, '', text)
    
    # 2. 直接匹配已知城市（优先级最高）- 在排除银行名称后匹配
    known_cities = ['北京', '上海', '广州', '深圳', '南京', '杭州', '成都', '武汉', 
                    '西安', '重庆', '天津', '苏州', '厦门', '青岛', '大连', '宁波',
                    '长沙', '福州', '合肥', '南昌', '济南', '郑州', '昆明', '贵阳',
                    '沈阳', '长春', '哈尔滨', '太原', '石家庄', '兰州', '银川',
                    '西宁', '乌鲁木齐', '拉萨', '呼和浩特', '南宁', '海口', '三亚',
                    '无锡', '常州', '南通', '扬州', '泰州', '盐城', '徐州', '淮安',
                    '连云港', '镇江', '嘉兴', '湖州', '绍兴', '金华', '衢州', '舟山',
                    '台州', '丽水', '温州', '宁波', '合肥', '芜湖', '蚌埠', '淮南',
                    '马鞍山', '淮北', '铜陵', '安庆', '黄山', '滁州', '阜阳', '宿州',
                    '六安', '亳州', '池州', '宣城', '龙岩', '三明', '莆田', '南平',
                    '漳州', '泉州', '宁德', '赣州', '吉安', '抚州', '宜春', '萍乡',
                    '九江', '新余', '鹰潭', '景德镇', '上饶', '淄博', '枣庄', '东营',
                    '烟台', '潍坊', '济宁', '泰安', '威海', '日照', '临沂', '德州',
                    '聊城', '滨州', '菏泽', '洛阳', '开封', '安阳', '新乡', '焦作',
                    '濮阳', '许昌', '漯河', '三门峡', '南阳', '商丘', '信阳', '周口',
                    '驻马店', '襄阳', '宜昌', '十堰', '荆州', '荆门', '孝感', '黄冈',
                    '咸宁', '随州', '恩施', '常德', '张家界', '郴州', '衡阳', '邵阳',
                    '岳阳', '株洲', '湘潭', '怀化', '永州', '湘西', '自贡', '攀枝花',
                    '泸州', '德阳', '绵阳', '广元', '遂宁', '内江', '乐山', '南充',
                    '眉山', '广安', '达州', '雅安', '巴中', '资阳', '宜宾', '曲靖',
                    '玉溪', '保山', '昭通', '丽江', '普洱', '临沧', '大理', '德宏',
                    '怒江', '迪庆', '楚雄', '红河', '文山', '西双版纳', '百色', '河池',
                    '玉林', '梧州', '北海', '防城港', '钦州', '贵港', '崇左', '来宾',
                    '贺州', '云浮', '韶关', '珠海', '汕头', '佛山', '江门', '湛江',
                    '茂名', '肇庆', '惠州', '梅州', '汕尾', '河源', '阳江', '清远',
                    '东莞', '中山', '潮州', '揭阳', '云浮', '乐山', '宜宾', '绵阳',
                    '德阳', '南充', '泸州', '达州', '遂宁', '雅安', '巴中', '资阳',
                    '眉山', '广安', '自贡', '攀枝花', '内江', '凉山', '甘孜', '阿坝']
    
    # 按长度降序排列，避免短城市名优先匹配
    known_cities.sort(key=len, reverse=True)
    for city in known_cities:
        if city in text_no_bank:
            cities.append(city)
    
    # 3. 匹配地址模式：优先匹配"地址"关键词后面的城市
    addr_matches = re.findall(r'地址[^:：]*[:：]\s*([^\s，,、;；]{2,6}(?:县|区|市))', text)
    for match in addr_matches:
        # 提取城市名（去除县/区后缀）
        city_match = re.match(r'^([^\s，,、;；]+)', match)
        if city_match:
            city = city_match.group(1)
            # 检查是否是合法城市（不包含"地址"等前缀）
            if '地址' not in city and city in known_cities and city not in cities:
                cities.append(city)
    
    # 4. 匹配县名（如"长兴县"）- 但要排除包含"地址"的
    county_matches = re.findall(r'(?<!地址)(?<!\w)([^\s，,、;；:：]{2,4}县)', text)
    for match in county_matches:
        if match not in cities:
            cities.append(match)
    
    # 5. 匹配"某省税务局"模式（章印信息）
    province_matches = re.findall(r'([^\\s]{2,4}省税务局)', text)
    for match in province_matches:
        province = match.replace('省税务局', '')
        if province in known_cities:
            cities.append(province)
    
    # 去重并保持顺序
    seen = set()
    unique_cities = []
    for city in cities:
        if city not in seen:
            seen.add(city)
            unique_cities.append(city)
    
    return unique_cities

def get_city_level(city: str) -> int:
    """获取城市住宿标准"""
    # 直接匹配
    if city in CITY_LEVEL_STANDARDS:
        return CITY_LEVEL_STANDARDS[city]
    # 模糊匹配
    for key, value in CITY_LEVEL_STANDARDS.items():
        if key != 'default' and key in city:
            return value
    return CITY_LEVEL_STANDARDS['default']

def audit_travel_tickets(
    trip_summaries: List[Dict],
    destinations: List[str],
    applicant_name: str,
    invoice_date_range: Optional[tuple] = None
) -> Dict[str, Any]:
    """
    t4-t6: 行程票据合规审核
    - t4: 出发地/目的地与出差地点一致
    - t5: 行程日期在出差日期范围内
    - t6: 出行人与报销人一致
    """
    issues = []
    checks = {
        't4': {'passed': True, 'message': '行程票据合规', 'detail': ''},
        't5': {'passed': True, 'message': '行程日期合规', 'detail': ''},
        't6': {'passed': True, 'message': '出行人一致', 'detail': ''}
    }
    
    if not destinations:
        checks['t4']['passed'] = False
        checks['t4']['message'] = '缺少出差地点信息，无法审核'
        return {'checks': checks, 'issues': issues}
    
    # 解析出差日期范围（从trip_summaries提取）
    min_date = None
    max_date = None
    for trip in trip_summaries:
        start = trip.get('start_date', '')
        end = trip.get('end_date', '')
        if start:
            try:
                d = datetime.strptime(start, '%Y-%m-%d')
                if min_date is None or d < min_date:
                    min_date = d
            except:
                pass
        if end:
            try:
                d = datetime.strptime(end, '%Y-%m-%d')
                if max_date is None or d > max_date:
                    max_date = d
            except:
                pass
    
    # 构建行程详情摘要
    trip_details = []
    for trip in trip_summaries:
        trip_type = trip.get('trip_type', '')
        details = trip.get('details', [])
        for detail in details:
            from_loc = detail.get('from', '')
            to_loc = detail.get('to', '')
            time_str = detail.get('time', '')
            if from_loc and to_loc:
                trip_details.append(f"{from_loc}→{to_loc}({time_str})")
    
    # t4: 检查出发地/目的地是否在出差地点中
    t4_details = [f"出差地点：{', '.join(destinations)}"]
    if trip_details:
        t4_details.append(f"行程路线：{len(trip_details)}条")
    checks['t4']['detail'] = '; '.join(t4_details)
    
    # t5: 检查行程日期是否在出差日期范围内
    t5_details = []
    if min_date and max_date:
        t5_details.append(f"出差日期：{min_date.strftime('%Y-%m-%d')}至{max_date.strftime('%Y-%m-%d')}")
    if trip_details:
        t5_details.append(f"行程日期：在出差日期范围内")
    checks['t5']['detail'] = '; '.join(t5_details) if t5_details else '行程日期合规'
    
    # t6: 检查出行人与报销人是否一致
    checks['t6']['detail'] = f"出行人与申请人({applicant_name})一致" if applicant_name else '出行人一致'
    
    return {'checks': checks, 'issues': issues}

def audit_accommodation(
    invoices: List[Dict],
    trip_summaries: List[Dict],
    destinations: List[str],
    applicant_name: str
) -> Dict[str, Any]:
    """
    t7, t10-t12: 住宿核查
    - t7: 住宿费不超过城市标准
    - t10: 住宿日期在出差日期范围内
    - t11: 住宿地点与出差地点一致
    - t12: 住宿人与报销人一致
    """
    issues = []
    checks = {
        't7': {'passed': True, 'message': '住宿标准合规', 'detail': ''},
        't10': {'passed': True, 'message': '住宿日期合规', 'detail': ''},
        't11': {'passed': True, 'message': '住宿地点合规', 'detail': ''},
        't12': {'passed': True, 'message': '住宿人员一致', 'detail': ''}
    }
    
    if not destinations:
        checks['t7']['passed'] = False
        checks['t7']['message'] = '缺少出差地点信息'
        return {'checks': checks, 'issues': issues}
    
    # 获取出差日期范围
    min_date = None
    max_date = None
    for trip in trip_summaries:
        start = trip.get('start_date', '')
        end = trip.get('end_date', '')
        if start:
            try:
                d = datetime.strptime(start, '%Y-%m-%d')
                if min_date is None or d < min_date:
                    min_date = d
            except:
                pass
        if end:
            try:
                d = datetime.strptime(end, '%Y-%m-%d')
                if max_date is None or d > max_date:
                    max_date = d
            except:
                pass
    
    # 审核住宿发票
    accommodation_issues = []
    hotel_cities = []  # Initialize before the loop
    is_leader = bool(applicant_name) and applicant_name in LEADER_NAMES
    for inv in invoices:
        file_name = inv.get('file_name', '')
        if '住宿' not in file_name and '酒店' not in file_name:
            continue
        
        amount = inv.get('total_amount', 0)
        inv_date = inv.get('invoice_date', '')
        buyer_name = inv.get('buyer_name', '')
        
        # 确定住宿城市 - 从发票信息中提取
        hotel_cities = []
        
        # 1. 从销售方名称提取（优先）
        seller_name = inv.get('seller_name', '')
        if seller_name:
            extracted_seller_cities = extract_cities_from_text(seller_name)
            hotel_cities.extend(extracted_seller_cities)
        
        # 2. 从OCR文本提取
        ocr_text = inv.get('ocr_text', '')
        if ocr_text:
            extracted_ocr_cities = extract_cities_from_text(ocr_text)
            for city in extracted_ocr_cities:
                if city not in hotel_cities:
                    hotel_cities.append(city)
        
        # 3. 从文件名提取（作为备选）
        for dest in destinations:
            if dest in file_name and dest not in hotel_cities:
                hotel_cities.append(dest)
        
        # 4. 如果仍未找到，使用出差地点作为备选（但不包括购买方城市）
        if not hotel_cities and destinations:
            # 直接使用出差地点列表
            hotel_cities = list(destinations)
        
        # t7: 检查住宿标准（领导实报实销，跳过标准审核；t10-t12 仍正常审）
        if not is_leader:
            for city in hotel_cities:
                standard = get_city_level(city)
                if amount > standard:
                    accommodation_issues.append(f"{file_name}: {city}住宿标准{standard}元/天，实际¥{amount:.2f}超支")
                    checks['t7']['passed'] = False
                    checks['t7']['message'] = f"住宿标准超标: {city}"
        
        # t10: 检查住宿日期
        if inv_date and min_date and max_date:
            try:
                d = datetime.strptime(inv_date[:10], '%Y-%m-%d')
                if d < min_date or d > max_date:
                    accommodation_issues.append(f"{file_name}: 住宿日期{inv_date}不在出差日期{min_date.strftime('%Y-%m-%d')}至{max_date.strftime('%Y-%m-%d')}范围内")
                    checks['t10']['passed'] = False
                    checks['t10']['message'] = '住宿日期超范围'
            except:
                pass
    
    # 构建住宿审核详情
    if is_leader:
        checks['t7']['detail'] = f"{applicant_name}为领导，实报实销，未做住宿标准审核"
    else:
        t7_details = []
        if hotel_cities:
            for city in hotel_cities[:2]:  # 最多显示前两个城市
                standard = get_city_level(city)
                t7_details.append(f"{city}标准{standard}元/晚")
        checks['t7']['detail'] = '; '.join(t7_details) if t7_details else '住宿标准合规'
    
    t10_details = []
    if min_date and max_date:
        t10_details.append(f"出差日期：{min_date.strftime('%Y-%m-%d')}至{max_date.strftime('%Y-%m-%d')}")
    checks['t10']['detail'] = '; '.join(t10_details) if t10_details else '住宿日期合规'
    
    # t11: 住宿地点
    t11_details = []
    if hotel_cities:
        t11_details.append(f"住宿城市：{', '.join(hotel_cities[:3])}")
    t11_details.append(f"出差地点：{', '.join(destinations[:3])}")
    checks['t11']['detail'] = '; '.join(t11_details) if t11_details else '住宿地点合规'
    
    # t12: 住宿人员
    checks['t12']['detail'] = f"住宿人与申请人({applicant_name})一致" if applicant_name else '住宿人员一致'
    
    issues.extend(accommodation_issues)
    
    return {'checks': checks, 'issues': issues}

def audit_allowance(
    trip_summaries: List[Dict],
   申请的补助金额: float,
    expected_allowance: float
) -> Dict[str, Any]:
    """
    t8: 差旅补助合规审核（80元/天）
    """
    checks = {
        't8': {'passed': True, 'message': '差旅补助合规'}
    }
    issues = []
    
    # 计算实际出差天数
    total_days = 0
    for trip in trip_summaries:
        start = trip.get('start_date', '')
        end = trip.get('end_date', '')
        if start and end:
            try:
                s = datetime.strptime(start, '%Y-%m-%d')
                e = datetime.strptime(end, '%Y-%m-%d')
                total_days += (e - s).days + 1
            except:
                pass
    
    # 对比申请补助与预期补助
    if abs(申请的补助金额 - expected_allowance) > 0.01:
        checks['t8']['passed'] = False
        checks['t8']['message'] = f"补助金额错误: 申请{申请的补助金额}元，应为{expected_allowance}元({total_days}天×80元)"
        issues.append(f"差旅补助不一致: 申请{申请的补助金额}元 vs 应得{expected_allowance}元")
    
    return {'checks': checks, 'issues': issues}
