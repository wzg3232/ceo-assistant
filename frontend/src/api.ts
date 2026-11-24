import axios from 'axios';
import type { ApprovalGroup, Statistics, Company } from './types';

const api = axios.create({
  baseURL: '/api',
});

export const fetchApprovals = async (): Promise<{ total: number; generated_at: string; groups: Record<string, ApprovalGroup> }> => {
  const res = await api.get('/approvals');
  return res.data;
};

export const fetchStatistics = async (): Promise<Statistics> => {
  const res = await api.get('/statistics');
  return res.data;
};

export const fetchCompanies = async (): Promise<{ companies: Company[] }> => {
  const res = await api.get('/companies');
  return res.data;
};

/** 审批详情(含类目逐笔对账 category_reconcile)。列表接口不含此字段, 点开对账时按需拉取。 */
export const fetchApprovalDetail = async (serial: string): Promise<ApprovalGroup> => {
  const res = await api.get(`/approvals/${serial}`);
  return res.data;
};

/* ===== 技能仓库 (SkillHub, 2026-11-09) ===== */

export interface SkillMeta {
  id: string;
  name: string;
  description: string;
  tags: string[];
  department: string;
  author: string;
  authorName: string;
  downloads: number;
  updatedBy: string;
  updatedAt: string | null;
  oneLiner: string;
  content?: string;
}

/* 部门分类(与后端 DEPARTMENTS 一致) */
export const DEPARTMENTS = ['财务', '行政', '采购', '电力交易', '投资', '通用', '年度工作计划'] as const;

export const fetchSkills = async (): Promise<SkillMeta[]> => {
  const res = await api.get('/skills');
  return res.data;
};

export const fetchSkillDetail = async (id: string): Promise<SkillMeta> => {
  const res = await api.get(`/skills/${id}`);
  return res.data;
};

export const createSkill = async (content: string, author: string, department: string = '通用'): Promise<{ ok: boolean; id: string }> => {
  const res = await api.post('/skills/create', { content, author, department });
  return res.data;
};

export const updateSkillContent = async (id: string, content: string, author: string, department?: string): Promise<void> => {
  await api.put(`/skills/${id}/content`, { content, author, department });
};

export const deleteSkill = async (id: string): Promise<void> => {
  await api.delete(`/skills/${id}`);
};

export const uploadSkillZip = async (file: File, author: string, department: string = '通用'): Promise<{ ok: boolean; id: string }> => {
  const form = new FormData();
  form.append('file', file);
  const res = await api.post(`/skills/upload-zip?author=${encodeURIComponent(author || '匿名')}&department=${encodeURIComponent(department)}`, form, {
    headers: { 'Content-Type': 'multipart/form-data' },
  });
  return res.data;
};

/* ===== 发票导出 (异步 zip: 合并PDF + OFD包 + XML包, 2026-11-19) ===== */
export interface InvoiceExportStatus {
  id: string;
  status: 'pending' | 'running' | 'done' | 'error';
  progress: number;
  current: string;
  errors?: string[];
  filename?: string;
  finished_at?: string;
}
export const startInvoiceExport = async (invoiceIds: number[]): Promise<string> => {
  const res = await api.post('/invoices/export', { invoice_ids: invoiceIds });
  return res.data.task_id;
};
export const getInvoiceExportStatus = async (taskId: string): Promise<InvoiceExportStatus> => {
  const res = await api.get(`/invoices/export/${taskId}`);
  return res.data;
};
export const downloadInvoiceExport = async (taskId: string, filename: string) => {
  const res = await api.get(`/invoices/export/${taskId}/download`, { responseType: 'blob' });
  const blobUrl = URL.createObjectURL(res.data);
  const a = document.createElement('a');
  a.href = blobUrl;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(blobUrl);
};

export { api };

/* ===== 单张发票 原件/OFD/XML 下载 (2026-11-19: 发票列表行内按钮) ===== */
export const downloadInvoiceFormat = async (invoiceId: number, fmt: 'pdf' | 'ofd' | 'xml') => {
  const res = await api.get(`/invoices/${invoiceId}/format/${fmt}`, { responseType: 'blob' });
  const blobUrl = URL.createObjectURL(res.data);
  const a = document.createElement('a');
  a.href = blobUrl;
  const cd: string = res.headers['content-disposition'] || '';
  const m = cd.match(/filename\*?=(?:UTF-8'')?([^;]+)/i);
  a.download = m ? decodeURIComponent(m[1]) : `invoice-${invoiceId}-${fmt}`;
  a.click();
  URL.revokeObjectURL(blobUrl);
};
