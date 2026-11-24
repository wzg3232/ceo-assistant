"""
审核规则定义 - 按分类组织
"""

# 费用报销审核规则（按分类）
EXPENSE_RULES_CATEGORIZED = {
    "发票合规": [
        {"id": "e1", "name": "发票购买方合规", "description": "发票购买方名称需包含公司全称，统一信用代码需与报销主体一致"},
        {"id": "e2", "name": "发票项目合规", "description": "发票内容需为正规发票，非清单或收据"},
    ],
    "金额核对": [
        {"id": "e3", "name": "金额一致", "description": "发票金额应与报销金额一致"},
    ],
}

# 差旅报销审核规则（按分类）
TRAVEL_RULES_CATEGORIZED = {
    "发票合规": [
        {"id": "t1", "name": "发票购买方合规", "description": "发票购买方名称需包含公司全称，统一信用代码需与报销主体一致"},
        {"id": "t2", "name": "发票项目合规", "description": "发票内容需为正规发票，非清单或收据"},
    ],
    "金额核对": [
        {"id": "t3", "name": "金额一致", "description": "发票金额应与报销金额一致"},
        {"id": "t9", "name": "清单与发票匹配", "description": "报销清单金额应与发票总额一致，清单项应与发票一一匹配"},
    ],
    "行程交通": [
        {"id": "t4", "name": "行程票据合规", "description": "火车票/飞机票出发地或目的地需与出差目的地一致"},
        {"id": "t5", "name": "行程日期合规", "description": "交通票据日期需在出差日期范围内"},
        {"id": "t6", "name": "出行人一致", "description": "交通票据乘客姓名需与报销人一致"},
        {"id": "t13", "name": "租车结算单", "description": "核对租车结算单金额、地点、时间", "dynamic": True},
    ],
    "住宿核查": [
        {"id": "t7", "name": "住宿标准合规", "description": "住宿费不得超过城市标准（一线400元/新一线350元/二线280元/三四线180元），领导实报实销不审核"},
        {"id": "t10", "name": "住宿日期合规", "description": "住宿费日期需在出差日期范围内"},
        {"id": "t11", "name": "住宿地点合规", "description": "住宿地点需与出差目的地一致"},
        {"id": "t12", "name": "住宿人员一致", "description": "住宿人需为报销本人"},
    ],
    "补助核对": [
        {"id": "t8", "name": "差旅补助合规", "description": "差旅补助按80元/天计算，需与申请补助金额一致"},
    ],
}

def get_rules(approval_type: str) -> dict:
    """根据审批类型返回审核规则（分类结构）。
    DB 真值为 '差旅费报销', 历史值 '差旅报销' 一并兼容(避免前端差旅专属Tab静默消失)。"""
    if approval_type in ("差旅费报销", "差旅报销"):
        return TRAVEL_RULES_CATEGORIZED
    else:
        return EXPENSE_RULES_CATEGORIZED

def get_flat_rules(approval_type: str) -> list:
    """返回扁平化的规则列表（兼容旧接口）"""
    rules_dict = get_rules(approval_type)
    flat = []
    for category, items in rules_dict.items():
        for item in items:
            item_copy = item.copy()
            item_copy["category"] = category
            flat.append(item_copy)
    return flat


# 报销主体配置
COMPANY_CONFIG = {
    "紫冬花能源科技有限公司": "91110119MABX263Y15"
}

def validate_buyer(invoice_data: dict) -> dict:
    """校验发票购买方信息，返回校验结果"""
    buyer_check = invoice_data.get('buyer_check', {})
    passed = buyer_check.get('passed', True)
    message = buyer_check.get('message', '')
    
    # 如果购买方检查已通过，再检查统一信用代码
    if passed:
        # 从OCR数据中获取购买方信息
        buyer_name = invoice_data.get('buyer_name', '')
        buyer_credit_code = invoice_data.get('buyer_credit_code', '')
        
        # 检查公司名称
        company_name_valid = '紫冬花能源科技有限公司' in buyer_name if buyer_name else False
        
        # 检查统一信用代码
        credit_code_valid = buyer_credit_code == COMPANY_CONFIG["紫冬花能源科技有限公司"] if buyer_credit_code else False
        
        if company_name_valid and credit_code_valid:
            return {'passed': True, 'message': '购买方信息合规'}
        elif company_name_valid:
            return {'passed': False, 'message': '统一信用代码不匹配'}
        elif credit_code_valid:
            return {'passed': False, 'message': '公司名称不匹配'}
        else:
            return {'passed': False, 'message': '购买方信息不合规'}
    
    return buyer_check
