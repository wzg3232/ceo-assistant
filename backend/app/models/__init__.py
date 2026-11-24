"""CEO助手 - SQLAlchemy数据库模型"""
from sqlalchemy import Column, Integer, BigInteger, String, Float, Boolean, DateTime, Text, ForeignKey, Index, UniqueConstraint
from sqlalchemy.orm import declarative_base
from sqlalchemy.orm import relationship, sessionmaker
from datetime import datetime
from typing import List as TypingList

Base = declarative_base()


class Company(Base):
    """公司表"""
    __tablename__ = 'companies'

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(200), unique=True, nullable=False, index=True)
    app_id = Column(String(100), nullable=False, default='')  # 飞书审批appId: cli_xxx
    app_id_short = Column(String(10), nullable=False, default='')  # 角标显示: "55500d"
    created_at = Column(DateTime, default=datetime.utcnow)

    approvals = relationship('Approval', back_populates='company')

    def to_dict(self):
        return {
            'id': self.id,
            'name': self.name,
            'app_id_short': self.app_id_short
        }


class Approval(Base):
    """审批单表"""
    __tablename__ = 'approvals'
    __table_args__ = (
        UniqueConstraint('serial_number', 'company_id', name='uq_approvals_serial_company'),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    serial_number = Column(String(50), nullable=False, index=True)  # 202607290005 (一次提交一个)
    approval_id = Column(String(100), nullable=False, index=True)  # 飞书实例UUID (同一实例可重新提交, 不再唯一)
    approval_uuid = Column(String(50), nullable=False)
    applicant_name = Column(String(100), nullable=False, index=True)
    approval_code = Column(String(100), nullable=False, default='')
    approval_type = Column(String(50), nullable=False, default='费用报销')  # 费用报销/差旅报销
    approval_amount = Column(Float, nullable=False, default=0)  # 审批金额
    total_invoice_amount = Column(Float, nullable=False, default=0)  # 发票总额
    start_time = Column(BigInteger, nullable=False)  # 毫秒时间戳
    audit_time = Column(String(50), nullable=True)  # 审核完成时间
    pass_count = Column(Integer, nullable=False, default=0)
    fail_count = Column(Integer, nullable=False, default=0)
    file_count = Column(Integer, nullable=False, default=0)
    invoice_count = Column(Integer, nullable=False, default=0)
    list_count = Column(Integer, nullable=False, default=0)
    status = Column(String(20), nullable=False, default='pending', index=True)  # pending/success/failed
    feishu_final_status = Column(String(20), nullable=True)  # 飞书最终状态(webhook终态事件写入, 2026-11-20)
    company_id = Column(Integer, ForeignKey('companies.id'), nullable=True)
    raw_data = Column(Text, nullable=True)  # 完整JSON备份
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    company = relationship('Company', back_populates='approvals')
    invoices = relationship('Invoice', back_populates='approval', cascade='all, delete-orphan')
    lists = relationship('ApprovalList', back_populates='approval', cascade='all, delete-orphan')
    trip_summaries = relationship('TripSummary', back_populates='approval', cascade='all, delete-orphan')
    issues = relationship('Issue', back_populates='approval', cascade='all, delete-orphan')

    def to_dict(self):
        return {
            'id': self.id,
            'serial_number': self.serial_number,
            'approval_id': self.approval_id,
            'approval_uuid': self.approval_uuid,
            'applicant_name': self.applicant_name,
            'approval_code': self.approval_code,
            'approval_type': self.approval_type,
            'approval_amount': self.approval_amount,
            'total_invoice_amount': self.total_invoice_amount,
            'start_time': self.start_time,
            'audit_time': self.audit_time,
            'pass_count': self.pass_count,
            'fail_count': self.fail_count,
            'file_count': self.file_count,
            'invoice_count': self.invoice_count,
            'list_count': self.list_count,
            'status': self.status,
            'feishu_final_status': getattr(self, 'feishu_final_status', None),  # 飞书最终状态
            'company_id': self.company_id,
            # 搜索命中来源 (发票号命中时前端显示徽标)
            'invoice_hit': getattr(self, '_invoice_hit', False),
        }


class Invoice(Base):
    """发票表"""
    __tablename__ = 'invoices'

    id = Column(Integer, primary_key=True, autoincrement=True)
    approval_id = Column(Integer, ForeignKey('approvals.id'), nullable=False, index=True)  # 指向 approvals.id (一次提交一条)
    file_name = Column(String(200), nullable=False)
    file_type = Column(String(20), nullable=False, default='invoice')  # invoice/list
    invoice_type = Column(String(50), nullable=True)  # 增值税电子普通发票等
    invoice_number = Column(String(100), nullable=True)
    invoice_date = Column(String(20), nullable=True)
    buyer_name = Column(String(200), nullable=True)
    total_amount = Column(Float, nullable=False, default=0)
    is_compliant = Column(Boolean, nullable=False, default=True)
    buyer_check_passed = Column(Boolean, nullable=True)
    buyer_check_message = Column(Text, nullable=True)
    items_check_passed = Column(Boolean, nullable=True)
    items_check_message = Column(Text, nullable=True)
    raw_json = Column(Text, nullable=True)  # 完整发票信息JSON
    created_at = Column(DateTime, default=datetime.utcnow)
    exported_at = Column(DateTime, nullable=True)  # 最近导出时间 (导出流程回写, 2026-11-24)
    export_history = Column(Text, nullable=True)  # 导出历史JSON数组 (2026-11-24): 每次批量导出/单张下载均追加, 末位=最近

    approval = relationship('Approval', back_populates='invoices')

    def _export_history_list(self):
        """导出历史时间列表 (JSON数组→list[str], 末位=最近; 无历史返回 None)。"""
        import json as _json
        try:
            v = _json.loads(self.export_history) if self.export_history else None
            return v if isinstance(v, list) else None
        except Exception:
            return None

    def _append_export_history(self, ts: str) -> None:
        """追加一条导出时间到 export_history (JSON数组), 供导出/单张下载端点回写。"""
        import json as _json
        hist = self._export_history_list() or []
        hist.append(ts)
        self.export_history = _json.dumps(hist, ensure_ascii=False)

    def to_dict(self):
        d = {
            'id': self.id,
            'file_name': self.file_name,
            'file_type': self.file_type,
            'invoice_type': self.invoice_type,
            'invoice_number': self.invoice_number,
            'invoice_date': self.invoice_date,
            'buyer_name': self.buyer_name,
            'total_amount': self.total_amount,
            'compliant': self.is_compliant,
            'exported_at': self.exported_at.strftime('%Y-%m-%d %H:%M:%S') if self.exported_at else None,
            'export_history': self._export_history_list(),
            'buyer_check': {
                'passed': self.buyer_check_passed,
                'message': self.buyer_check_message
            } if self.buyer_check_passed is not None else None,
            'items_check': {
                'passed': self.items_check_passed,
                'message': self.items_check_message
            } if self.items_check_passed is not None else None,
        }
        # 回传 raw_json 中按发票类型实际执行的专项检查 (对齐合规审核依据)
        SPECIAL_CHECKS = [
            ('taxi_trip_check', '打车行程单'),
            ('car_rental_check', '租车结算单'),
            ('transport_check', '交通票据'),
            ('passenger_check', '出行人信息'),
            ('travel_check', '住宿费标准'),
            ('accommodation_check', '入住信息单'),
        ]
        raw = {}
        if self.raw_json:
            try:
                import json as _json
                raw = _json.loads(self.raw_json)
            except Exception:
                raw = {}
        for key, label in SPECIAL_CHECKS:
            v = raw.get(key)
            if isinstance(v, dict) and 'passed' in v:
                d[key] = {
                    'passed': v.get('passed'),
                    'message': v.get('message', ''),
                    'label': label,
                }
        return d


class ApprovalList(Base):
    """清单表（避免与Python list关键字冲突）"""
    __tablename__ = 'approval_lists'

    id = Column(Integer, primary_key=True, autoincrement=True)
    approval_id = Column(Integer, ForeignKey('approvals.id'), nullable=False, index=True)
    file_name = Column(String(200), nullable=False)
    list_total = Column(Float, nullable=True)
    invoice_total = Column(Float, nullable=True)
    list_items_count = Column(Integer, nullable=True)
    invoice_count = Column(Integer, nullable=True)
    is_match = Column(Boolean, nullable=True)  # 清单与发票是否匹配
    match_issues = Column(Text, nullable=True)  # 不匹配的问题JSON数组
    raw_json = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    approval = relationship('Approval', back_populates='lists')
    list_items = relationship('ListItem', back_populates='approval_list', cascade='all, delete-orphan')

    def to_dict(self):
        # 解析issues为JSON数组
        issues = []
        if self.match_issues:
            try:
                import json
                issues = json.loads(self.match_issues) if isinstance(self.match_issues, str) else self.match_issues
            except:
                issues = []
        return {
            'id': self.id,
            'file_name': self.file_name,
            'list_total': self.list_total,
            'invoice_total': self.invoice_total,
            'list_items_count': self.list_items_count,
            'invoice_count': self.invoice_count,
            'match': self.is_match,
            'issues': issues
        }


class ListItem(Base):
    """清单明细项表"""
    __tablename__ = 'list_items'

    id = Column(Integer, primary_key=True, autoincrement=True)
    list_id = Column(Integer, ForeignKey('approval_lists.id'), nullable=False, index=True)
    date = Column(String(20), nullable=True)
    from_place = Column(String(200), nullable=True)
    to_place = Column(String(200), nullable=True)
    item_type = Column(String(50), nullable=True)  # transport/hotel/subsidy/other
    transport = Column(Float, nullable=False, default=0)
    hotel = Column(Float, nullable=False, default=0)
    subsidy = Column(Float, nullable=False, default=0)
    other_transport = Column(Float, nullable=False, default=0)
    other = Column(Float, nullable=False, default=0)
    remark = Column(String(300), nullable=True)  # 备注列原文 (如"王剑，邱宇等人的餐费")
    total = Column(Float, nullable=False, default=0)
    created_at = Column(DateTime, default=datetime.utcnow)

    approval_list = relationship('ApprovalList', back_populates='list_items')

    def to_dict(self):
        return {
            'date': self.date,
            'from': self.from_place,
            'to': self.to_place,
            'type': self.item_type,
            'transport': self.transport,
            'hotel': self.hotel,
            'subsidy': self.subsidy,
            'other_transport': self.other_transport,
            'other': self.other,
            'remark': self.remark,
            'total': self.total
        }


class TripSummary(Base):
    """行程汇总表"""
    __tablename__ = 'trip_summaries'

    id = Column(Integer, primary_key=True, autoincrement=True)
    approval_id = Column(Integer, ForeignKey('approvals.id'), nullable=False, index=True)
    file_name = Column(String(200), nullable=True)
    page = Column(Integer, nullable=True)
    trip_type = Column(String(50), nullable=True)  # 滴滴出行/曹操出行
    trips = Column(Integer, nullable=True)  # 次数
    total_amount = Column(Float, nullable=False, default=0)
    start_date = Column(String(20), nullable=True)
    end_date = Column(String(20), nullable=True)
    phone = Column(String(50), nullable=True)
    raw_json = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    approval = relationship('Approval', back_populates='trip_summaries')
    details = relationship('TripDetail', back_populates='summary', cascade='all, delete-orphan')

    def to_dict(self):
        return {
            'file_name': self.file_name,
            'page': self.page,
            'trip_type': self.trip_type,
            'trips': self.trips,
            'total_amount': self.total_amount,
            'start_date': self.start_date,
            'end_date': self.end_date,
            'phone': self.phone,
            'details': [d.to_dict() for d in self.details] if self.details else []
        }


class TripDetail(Base):
    """行程单每笔明细表（网约车行程单: 序号/车型/时间/城市/起点/终点/里程/金额）"""
    __tablename__ = 'trip_details'

    id = Column(Integer, primary_key=True, autoincrement=True)
    trip_summary_id = Column(Integer, ForeignKey('trip_summaries.id'), nullable=False, index=True)
    seq = Column(Integer, nullable=True)
    car_type = Column(String(50), nullable=True)   # 车型
    trip_time = Column(String(50), nullable=True)  # 上车时间
    city = Column(String(50), nullable=True)
    from_place = Column(String(300), nullable=True)
    to_place = Column(String(300), nullable=True)
    distance = Column(String(20), nullable=True)   # 里程[公里]
    amount = Column(String(20), nullable=True)      # 金额[元]
    created_at = Column(DateTime, default=datetime.utcnow)

    summary = relationship('TripSummary', back_populates='details')

    def to_dict(self):
        return {
            'seq': self.seq,
            'type': self.car_type,
            'time': self.trip_time,
            'city': self.city,
            'from': self.from_place,
            'to': self.to_place,
            'distance': self.distance,
            'amount': self.amount,
        }


class Issue(Base):
    """问题明细表"""
    __tablename__ = 'issues'

    id = Column(Integer, primary_key=True, autoincrement=True)
    approval_id = Column(Integer, ForeignKey('approvals.id'), nullable=False, index=True)
    file_name = Column(String(200), nullable=True)
    issue_type = Column(String(50), nullable=True)  # 发票/清单/购买方等
    messages = Column(Text, nullable=True)  # JSON数组
    created_at = Column(DateTime, default=datetime.utcnow)

    approval = relationship('Approval', back_populates='issues')

    def to_dict(self):
        return {
            'file': self.file_name,
            'type': self.issue_type,
            'messages': self.messages
        }


class AuditLog(Base):
    """操作日志表"""
    __tablename__ = 'audit_logs'

    id = Column(Integer, primary_key=True, autoincrement=True)
    approval_id = Column(String(100), nullable=True, index=True)
    action = Column(String(50), nullable=False)  # audit/start/review/complete
    operator = Column(String(100), nullable=True)
    detail = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    def to_dict(self):
        return {
            'id': self.id,
            'approval_id': self.approval_id,
            'action': self.action,
            'operator': self.operator,
            'created_at': self.created_at.isoformat() if self.created_at else None
        }


class Skill(Base):
    """技能仓库表 (SkillHub, 2026-11-09)"""
    __tablename__ = 'skills'

    id = Column(String(64), primary_key=True)  # slug: 小写字母数字连字符
    name = Column(String(200), nullable=False)
    description = Column(Text, nullable=False, default='')
    tags = Column(String(500), nullable=False, default='')  # 逗号分隔
    department = Column(String(50), nullable=False, default='通用')  # 部门分类
    author = Column(String(100), nullable=False, default='匿名')
    author_name = Column(String(100), nullable=False, default='')
    content = Column(Text, nullable=False, default='')  # SKILL.md 全文
    downloads = Column(Integer, nullable=False, default=0)
    updated_by = Column(String(100), nullable=False, default='')
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def to_dict(self, with_content=False):
        d = {
            'id': self.id,
            'name': self.name,
            'description': self.description,
            'tags': [t for t in (self.tags or '').split(',') if t],
            'department': self.department or '通用',
            'author': self.author,
            'authorName': self.author_name or self.author,
            'downloads': self.downloads,
            'updatedBy': self.updated_by,
            'updatedAt': self.updated_at.isoformat() if self.updated_at else None,
        }
        if with_content:
            d['content'] = self.content
        return d
