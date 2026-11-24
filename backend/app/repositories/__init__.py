"""CEO助手 - 数据访问层（Repository模式）"""
from typing import List, Optional, Dict, Any
from sqlalchemy.orm import Session
from sqlalchemy import desc, func
from datetime import datetime
import json

from app.models import Company, Approval, Invoice, ApprovalList, TripSummary, Issue


class ApprovalRepository:
    """审批单数据访问"""
    
    def __init__(self, db: Session):
        self.db = db
    
    def get_all(self, company_id: Optional[int] = None) -> List[Approval]:
        """获取所有审批单"""
        query = self.db.query(Approval)
        if company_id:
            query = query.filter(Approval.company_id == company_id)
        return query.order_by(desc(Approval.start_time)).all()
    
    def get_by_serial(self, serial_number: str) -> Optional[Approval]:
        """根据编号查找"""
        return self.db.query(Approval).filter(Approval.serial_number == serial_number).first()
    
    def get_by_uuid(self, approval_uuid: str) -> Optional[Approval]:
        """根据UUID查找"""
        return self.db.query(Approval).filter(Approval.approval_uuid == approval_uuid).first()
    
    def get_statistics(self, company_id: Optional[int] = None) -> Dict[str, Any]:
        """获取统计数据"""
        query = self.db.query(Approval)
        if company_id:
            query = query.filter(Approval.company_id == company_id)
        
        total = query.count()
        success = query.filter(Approval.status == 'success').count()
        failed = query.filter(Approval.status == 'failed').count()
        total_amount = query.with_entities(func.sum(Approval.approval_amount)).scalar() or 0
        
        return {
            'total': total,
            'success': success,
            'failed': failed,
            'total_amount': float(total_amount),
            'generated_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        }
    
    def search(self, keyword: str, status: Optional[str] = None) -> List[Approval]:
        """搜索审批单 (申请人/单号/发票号三通道)

        发票号通道: 通过 invoices.invoice_number 精确匹配, 命中后把该审批单
        加入结果集并标记 _invoice_hit=True (前端可显示"发票号命中"徽标)。
        """
        # 申请人 + 单号通道
        q1 = self.db.query(Approval).filter(
            (Approval.applicant_name.contains(keyword)) |
            (Approval.serial_number.contains(keyword))
        )
        # 发票号通道 (精确匹配, invoice_number 是 20 位数字串)
        inv_hits = (self.db.query(Invoice.approval_id)
                    .filter(Invoice.invoice_number == keyword).distinct().all())
        ids_from_inv = {h[0] for h in inv_hits}
        approvals = q1.all()
        # 把发票号命中的审批单补进来 (不重复, 不去重后标记 hit)
        known = {a.id for a in approvals}
        if ids_from_inv:
            extra = self.db.query(Approval).filter(Approval.id.in_(ids_from_inv)).all()
            approvals += [a for a in extra if a.id not in known]
        if status:
            approvals = [a for a in approvals if a.status == status]
        approvals.sort(key=lambda a: a.start_time or 0, reverse=True)
        # 标记哪些行是因发票号命中的
        for a in approvals:
            a._invoice_hit = a.id in ids_from_inv
        return approvals
    
    def get_invoices(self, approval_pk: int) -> List[Invoice]:
        """获取审批单的发票列表 (approval_pk = approvals.id 主键)"""
        return self.db.query(Invoice).filter(
            Invoice.approval_id == approval_pk
        ).all()
    
    def get_lists(self, approval_pk: int) -> List[ApprovalList]:
        """获取审批单的清单列表"""
        return self.db.query(ApprovalList).filter(
            ApprovalList.approval_id == approval_pk
        ).all()

    def get_list_items(self, list_id: int) -> List[Any]:
        """获取清单明细项"""
        from app.models import ListItem
        return self.db.query(ListItem).filter(
            ListItem.list_id == list_id
        ).all()
    
    def get_trips(self, approval_pk: int) -> List[TripSummary]:
        """获取审批单的行程汇总 (含每笔明细)"""
        return self.db.query(TripSummary).filter(
            TripSummary.approval_id == approval_pk
        ).all()
    
    def get_issues(self, approval_pk: int) -> List[Issue]:
        """获取审批单的问题明细"""
        return self.db.query(Issue).filter(
            Issue.approval_id == approval_pk
        ).all()
    
    def get_full_approval(self, approval_pk: int) -> Optional[Dict[str, Any]]:
        """获取完整的审批单信息（包含发票、清单、行程、问题）"""
        approval = self.db.query(Approval).filter(Approval.id == approval_pk).first()
        if not approval:
            return None
        
        return {
            **approval.to_dict(),
            'invoices': [i.to_dict() for i in self.get_invoices(approval_pk)],
            'lists': [l.to_dict() for l in self.get_lists(approval_pk)],
            'trip_summaries': [t.to_dict() for t in self.get_trips(approval_pk)],
            'issues': [i.to_dict() for i in self.get_issues(approval_pk)]
        }


class CompanyRepository:
    """公司数据访问"""
    
    def __init__(self, db: Session):
        self.db = db
    
    def get_all(self) -> List[Company]:
        """获取所有公司"""
        return self.db.query(Company).all()
    
    def get_by_name(self, name: str) -> Optional[Company]:
        """根据名称查找"""
        return self.db.query(Company).filter(Company.name == name).first()
