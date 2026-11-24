export interface InvoiceItem {
  id: number; // DB主键 (异步导出用, 2026-11-19)
  exported_at?: string; // 最近导出时间 "YYYY-MM-DD HH:MM:SS" (后端回写, 2026-11-24; 未导出为 null)
  export_history?: string[]; // 导出历史时间数组 (2026-11-24): 每次批量导出/单张下载均追加, 末位=最近; 未导出为 null
  file_name: string;
  file_type: 'invoice' | 'list' | 'transport' | 'receipt';
  is_invoice: boolean;
  status: string;
  compliant: boolean;
  buyer_check: {
    passed: boolean;
    message: string;
    issues?: string[];
    detail?: string;
  };
  items_check: {
    passed: boolean;
    message: string;
    detail?: string;
  };
  // 按发票类型实际执行的专项检查 (来自 raw_json, 后端 to_dict 回传)
  taxi_trip_check?: { passed: boolean | null; message: string; label?: string };
  car_rental_check?: { passed: boolean | null; message: string; label?: string };
  transport_check?: { passed: boolean | null; message: string; label?: string };
  passenger_check?: { passed: boolean | null; message: string; label?: string };
  travel_check?: { passed: boolean | null; message: string; label?: string };
  accommodation_check?: { passed: boolean | null; message: string; label?: string };
  invoice_type: string;
  invoice_number: string;
  invoice_date: string;
  buyer_name: string;
  buyer_credit_code?: string;
  seller_name?: string;
  seller_credit_code?: string;
  item_name?: string;
  invoice_class?: string;  // 五分类(专票/铁路/航空/旅客运输服务/普票), 识别不出为空串 (2026-11-23)
  total_amount: number;
  amount?: number;
  ocr_text?: string;
}

export interface TripSummary {
  file_name: string;
  page?: number;
  pages?: number;
  trip_type?: string;
  type?: string;
  trips?: number;
  total_amount: number;
  start_date?: string;
  end_date?: string;
  phone?: string;
  route?: string;
  details?: Array<{
    seq: string;
    type: string;
    time: string;
    city: string;
    from: string;
    to: string;
    distance: string;
    amount: string;
  }>;
}

export interface ListVerifyResult {
  list_total: number;
  invoice_total: number;
  list_items_count: number;
  invoice_count: number;
  match: boolean;
  issues: Array<{
    type: string;
    message: string;
  }>;
  list_items: Array<{
    date: string;
    from: string;
    to: string;
    type: string;
    transport: number;
    hotel: number;
    subsidy?: number;
    other_transport: number;
    other: number;
    remark?: string;
    total: number;
  }>;
  approval_amount?: number;
  approval_match?: boolean;
  travel_allowance?: number;
  comparable_amount?: number;
  travel_allowance_check?: {
    passed: boolean;
    message: string;
  };
  travel_dates?: string[];
  expected_allowance?: number;
}

export interface RentalCheckResult {
  found: boolean;
  status: 'pass' | 'fail';
  message: string;
  details?: {
    count: number;
    files: string[];
    issues?: string[];
    warnings?: string[];
  };
}

/** 类目逐笔对账 (2026-11-03): 清单行 vs 发票 逐笔配对结果 */
export interface CategoryReconcileRow {
  amount: number;
  desc: string;
  date?: string;
  from_place?: string;
  to_place?: string;
  item_type?: string;
  remark?: string;
  invoice_number?: string;
  invoice_type?: string;
  file_name?: string;
}

export interface CategoryReconcileItem {
  category: string;
  list: number;
  invoice: number;
  form: number | null;
  diff: number;
  passed: boolean;
  /** diff=总额不一致(需关注) / info=总额一致(逐笔仅参考, 可能合并开票) */
  mode: 'diff' | 'info';
  form_passed: boolean | null;
  list_count: number;
  invoice_count: number;
  matched: Array<{ list: CategoryReconcileRow; invoice: CategoryReconcileRow; by: string }>;
  list_only: CategoryReconcileRow[];
  invoice_only: CategoryReconcileRow[];
  list_only_total: number;
  invoice_only_total: number;
  list_only_sum: number;
  invoice_only_sum: number;
  note: string;
}

export interface CategoryReconcileResult {
  categories: CategoryReconcileItem[];
  summary: {
    failed_categories: string[];
    passed: boolean;
    total_matched: number;
    total_list_only: number;
    total_invoice_only: number;
  };
}

export interface ApprovalGroup {
  serial_number: string;
  approval_id: string;
  approval_uuid: string;
  applicant_name: string;
  approval_code: string;
  approval_type: string;
  approval_amount: number;
  total_invoice_amount: number;
  start_time: number;
  audit_time: string;
  pass_count: number;
  fail_count: number;
  file_count: number;
  invoice_count: number;
  list_count: number;
  status: 'success' | 'failed';
  feishu_final_status?: string | null;  // 飞书最终状态(PENDING/APPROVED/TERMINATED, null=未记录)
  invoice_numbers?: string[];
  invoice_hit?: boolean;
  has_duplicate_invoice?: string | null;
  invoices: InvoiceItem[];
  lists: InvoiceItem[];
  trip_summaries?: TripSummary[];
  trip_destinations?: string;
  travel_dates?: { start: string; end: string };
  travel_check?: {
    passed: boolean;
    message: string;
    checks?: Record<string, { passed: boolean; message: string }>;
    issues?: string[];
  };
  accommodation_check?: {
    passed: boolean;
    message: string;
    checks?: Record<string, { passed: boolean; message: string }>;
    issues?: string[];
  };
  allowance_check?: {
    passed: boolean;
    message: string;
    checks?: Record<string, { passed: boolean; message: string }>;
    issues?: string[];
  };
  issues: Array<{
    file: string;
    type: string;
    messages: string[];
  }>;
  list_verify?: ListVerifyResult;
  rental_check?: RentalCheckResult;
  /** 类目逐笔对账(差旅费报销, 读端计算) */
  category_reconcile?: CategoryReconcileResult | null;
  /** 飞书表单可展示字段 (2026-11-17, 基本信息扩展区) */
  form_fields?: Array<{ label: string; value: string | number }>;
  /** 表单 attachmentV2 附件原件 (2026-11-17, 走 /files/ 缓存+飞书回源) */
  form_attachments?: Array<{ filename: string; url: string; cached: boolean }>;
  rules?: Array<{
    id: string;
    name: string;
    description: string;
  }>;
}

export interface Statistics {
  total: number;
  success: number;
  failed: number;
  total_amount: number;
  generated_at: string;
}

export interface Company {
  name: string;
  app_id_short: string;
}
