import { useState, useEffect, useRef } from 'react';
import { fetchApprovals, fetchStatistics, fetchCompanies, fetchApprovalDetail, startInvoiceExport, getInvoiceExportStatus, downloadInvoiceExport, downloadInvoiceFormat } from './api';
import type { ApprovalGroup, Company, CategoryReconcileItem } from './types';
import SkillsView from './SkillsView';
import './App.css';

function App() {
  // 主视图切换: 审批看板·部门二级 (2026-11-24: 财务部/采购部/行政部) + 技能仓库 (2026-11-09)
  const [view, setView] = useState<'finance' | 'procurement' | 'admin' | 'skills'>('finance');
  const [boardOpen, setBoardOpen] = useState(true);
  const [sideCollapsed, setSideCollapsed] = useState<boolean>(() => {
    try { return localStorage.getItem('sideCollapsed') === '1'; } catch { return false; }
  });
  const toggleSide = () => setSideCollapsed(c => {
    try { localStorage.setItem('sideCollapsed', c ? '0' : '1'); } catch {}
    return !c;
  });
  const [approvals, setApprovals] = useState<ApprovalGroup[]>([]);
  // 审批看板按部门范围 (2026-11-24): 财务部只处理 费用报销/差旅费报销; 采购部/行政部 暂未接入 (空范围=空态)
  const DEPT_TYPES: Record<string, string[]> = {
    finance: ['费用报销', '差旅费报销'],
    procurement: [],
    admin: [],
  };
  const DEPT_NAMES: Record<string, string> = { finance: '财务部', procurement: '采购部', admin: '行政部' };
  const deptTypes = DEPT_TYPES[view] || [];
  const boardApprovals = approvals.filter((g) => deptTypes.includes(g.approval_type));
  const [stats, setStats] = useState<any>(null);
  const [companies, setCompanies] = useState<Company[]>([]);
  const [selectedCompany, setSelectedCompany] = useState<string>('');
  const [filter, setFilter] = useState<'all' | 'success' | 'failed'>('all');
  const [search, setSearch] = useState('');
  // 主区 Tab: 审批列表 / 发票列表 (2026-11-06)
  const [mainTab, setMainTab] = useState<'approvals' | 'invoices'>('approvals');
  // 发票列表: 时间段筛选 (按发票开票日) + 导出 (默认全部, 只导筛选范围内的)
  const [invFrom, setInvFrom] = useState('');
  const [invTo, setInvTo] = useState('');
  const [invPage, setInvPage] = useState(1); // 发票列表分页 (2026-11-19: 与审批列表一致, 每页20条)
  const [selectedGroup, setSelectedGroup] = useState<ApprovalGroup | null>(null);
  const [approvalPage, setApprovalPage] = useState(1); // 审批列表分页 (2026-11-19: 首屏一次性渲染全量表格太慢, 改为每页20条)
  const [duplicateGroups, setDuplicateGroups] = useState<ApprovalGroup[]>([]);
  const [showDuplicateDialog, setShowDuplicateDialog] = useState(false);
  // 类目逐笔对账 (2026-11-03): 结果内嵌在右侧"问题明细"区, 点按钮懒加载数据后展开
  const [crOpen, setCrOpen] = useState(false);
  const [crExpanded, setCrExpanded] = useState<Set<string>>(new Set());
  const [crLoading, setCrLoading] = useState(false);
  const [expandedTrips, setExpandedTrips] = useState<Set<number>>(new Set());
  const [expandedInvoices, setExpandedInvoices] = useState<Set<number>>(new Set());
  const [activeComplianceTab, setActiveComplianceTab] = useState<string>('发票合规');
  const [activeDialogTab, setActiveDialogTab] = useState<string>('发票明细');
  // 发票明细tab仅展示正式发票(file_type='invoice')；2026-10-14起通行费/过路费收据不再计入发票明细，
  // 与写端总额口径(仅正式发票)一致；行程单(file_type='transport')仅展示在"行程单汇总"tab
  const invoicesOnly = selectedGroup?.invoices?.filter((i) => i.file_type === 'invoice') ?? [];
  // 其他单据tab展示全部receipt类型票据(含通行费/过路费收据)
  const receiptsOnly = selectedGroup?.invoices?.filter((i) => i.file_type === 'receipt') ?? [];
  // 其他单据：按文件名/发票类型推导单据类型（过路费/租车结算单/水单等），不展示OCR发票类型名称
  const receiptTypeName = (file_name?: string, invoice_type?: string) => {
    const name = file_name || '';
    const itype = invoice_type || '';
    if (itype.includes('通行费') || itype.includes('过路费')) return '通行费';
    if (name.includes('过路费')) return '过路费';
    if (name.includes('租车')) return '租车结算单';
    if (name.includes('水单')) return '住宿水单';
    if (name.includes('收据')) return '收据';
    return '其他单据';
  };

  useEffect(() => {
    loadData();
  }, []);

  // 深链自动打开详情 (2026-10-23): 飞书通知里的看板链接带 ?serial=xxx,
  // 前端据此起步自动定位并打开该单详情弹框; 用 ref 保证一次性, 并清理地址栏参数避免刷新重开
  const deepLinkHandled = useRef(false);
  useEffect(() => {
    if (deepLinkHandled.current || approvals.length === 0) return;
    const target = new URLSearchParams(window.location.search).get('serial');
    if (!target) return;
    const group = approvals.find((g) => g.serial_number === target);
    if (group) {
      setSelectedGroup(group);
      deepLinkHandled.current = true;
      const url = new URL(window.location.href);
      url.searchParams.delete('serial');
      window.history.replaceState({}, '', url.pathname);
    } else {
      // 列表已加载但无匹配单(不存在): 标记处理避免后续 SSE 刷新反复查找
      deepLinkHandled.current = true;
      const url = new URL(window.location.href);
      url.searchParams.delete('serial');
      window.history.replaceState({}, '', url.pathname);
    }
  }, [approvals]);

  const loadData = async (isAutoRefresh = false) => {
    try {
      const [approvalsData, statsData, companiesData] = await Promise.all([
        fetchApprovals(),
        fetchStatistics(),
        fetchCompanies()
      ]);
      setApprovals(Object.values(approvalsData.groups));
      setStats(statsData);
      const companyList = companiesData.companies || [];
      setCompanies(companyList);
      // 手动/首次加载：默认选中第一个公司；自动刷新：保留用户当前选择
      if (companyList.length > 0) {
        const currentInList = companyList.some((c) => c.name === selectedCompany);
        if (!isAutoRefresh || !selectedCompany || !currentInList) {
          setSelectedCompany(companyList[0].name);
        }
      }
    } catch (err) {
      console.error('加载数据失败:', err);
    }
  };

  // 事件驱动刷新：后端在写入 Postgres 后推送通知，前端收到即重新拉取列表
  // 另加 30s 轮询兜底：SSE ping 只在 B机 8002 生效，其他机器的面板靠轮询保持自动更新
  const loadDataRef = useRef<(auto?: boolean) => void>(() => {});
  loadDataRef.current = loadData;
  useEffect(() => {
    let es: EventSource | null = null;
    try {
      es = new EventSource('/api/events/stream');
      es.onmessage = () => loadDataRef.current(true);
    } catch (e) {
      console.warn('事件刷新通道不可用:', e);
    }
    const poll = window.setInterval(() => loadDataRef.current(true), 30000);
    return () => { if (es) es.close(); window.clearInterval(poll); };
  }, []);

  // 部门口径统计 (2026-11-24): 统计卡跟随左侧部门筛选, 不再用全量 stats
  const deptStats = {
    total: boardApprovals.length,
    success: boardApprovals.filter((g) => g.status === 'success').length,
    failed: boardApprovals.filter((g) => g.status === 'failed').length,
    amount: boardApprovals.reduce((s, g) => s + (g.approval_amount || 0), 0),
  };
  const filteredApprovals = boardApprovals.filter(group => {
    const matchesFilter = 
      filter === 'all' || 
      (filter === 'success' && group.status === 'success') ||
      (filter === 'failed' && group.status === 'failed');
    const matchesSearch = 
      !search || 
      group.serial_number.includes(search) ||
      group.applicant_name.includes(search) ||
      group.invoice_numbers?.some((n: string) => n?.includes(search));
    return matchesFilter && matchesSearch;
  }).sort((a, b) => {
    // 按 start_time 逆序排序（最新的在前）
    const timeA = a.start_time || 0;
    const timeB = b.start_time || 0;
    return timeB - timeA;
  });

  // 审批列表分页 (2026-11-19: 首屏一次性渲染全量表格太慢, 改为每页20条)
  const PAGE_SIZE = 20;
  const approvalTotalPages = Math.max(1, Math.ceil(filteredApprovals.length / PAGE_SIZE));
  const safeApprovalPage = Math.min(approvalPage, approvalTotalPages); // 数据减少时防越界
  const pagedApprovals = filteredApprovals.slice((safeApprovalPage - 1) * PAGE_SIZE, safeApprovalPage * PAGE_SIZE);
  useEffect(() => { setApprovalPage(1); }, [filter, search, selectedCompany, view]);

  const formatDate = (timestamp: number): string => {
    if (!timestamp) return '-';
    // 兼容毫秒/秒: 库中 start_time 为毫秒级(BigInteger), 旧数据可能为秒级
    const ms = timestamp > 1e12 ? timestamp : timestamp * 1000;
    return new Date(ms).toLocaleString('zh-CN', {
      year: 'numeric',
      month: '2-digit',
      day: '2-digit',
      hour: '2-digit',
      minute: '2-digit'
    });
  };

  const copyLink = (group: ApprovalGroup) => {
    const instanceId = group.approval_id || group.approval_uuid || '';
    const feishuUrl = `https://applink.feishu.cn/client/mini_program/open?appId=cli_9cb844403dbb9108&mode=appCenter&path=pc/pages/in-process/index?enableTrusteeship=true&instanceId=${instanceId}&source=approval_bot&relaunch=true`;
    window.open(feishuUrl, '_blank');
  };

  // ---- 发票列表 Tab (2026-11-06): 聚合所有审批单 发票明细tab(file_type='invoice') 的发票行 ----
  // 发票类型: 普票/专票 (按 invoice_type 是否含"专用"), 其他单据单独一档 (2026-11-06 调整)
  const [invTypeFilter, setInvTypeFilter] = useState<string>('all');  // all | 七个中文分类名(后端 invoice_class, 非标准票面归'其他')
  // 飞书状态筛选 (2026-11-20): 目前仅"已通过"一项, 置灰不可切换, 列表只显示 APPROVED 的发票文件
  const [invFeishuFilter, setInvFeishuFilter] = useState<'approved'>('approved');
  // 发票号搜索 (2026-11-23): 按发票号码子串匹配 (大小写不敏感), 与其他筛选叠加
  const [invNoSearch, setInvNoSearch] = useState('');
  // invoice_class: 后端按五类归类(专票/铁路/航空/普票-运输/普票), 识别不出的留空 (2026-11-23)
  // 单张发票可下载格式 (2026-11-19): 按原件扩展名推导 — pdf原件→PDF, ofd原件→OFD+XML(内嵌提取)
  const dlFormats = (r: any) => {
    const ext = (r.file_name || '').toLowerCase().split('.').pop() || '';
    return { pdf: ext === 'pdf', ofd: ext === 'ofd', xml: ext === 'ofd' };
  };
  // 归一开票日 yyyy-mm-dd (库内混合格式: 2026-07-10 / 2026/07/10 / 2026年07月10日 / 仅到月 2026年07 → 当月01日)
  const normDate = (d?: string) => {
    if (!d) return '';
    let m = (d.trim()).match(/(\d{4})[-/年.](\d{1,2})[-/月.](\d{1,2})/);
    if (m) return `${m[1]}-${m[2].padStart(2, '0')}-${m[3].padStart(2, '0')}`;
    m = (d.trim()).match(/(\d{4})[-/年.](\d{1,2})月?/);
    if (m) return `${m[1]}-${m[2].padStart(2, '0')}-01`;
    return '';
  };
  const flatInvoices = boardApprovals.flatMap((g) =>
    (g.invoices || [])
      .map((i) => ({
        id: i.id,
        serial: g.serial_number,
        applicant: g.applicant_name,
        approval_type: g.approval_type,
        status: g.status,
        file_name: i.file_name,
        file_type: i.file_type,
        invoice_class: i.invoice_class || '',
        invoice_type: i.invoice_type || '',
        invoice_number: i.invoice_number || '',
        invoice_date: normDate(i.invoice_date),
        buyer: i.buyer_name || '',
        seller: i.seller_name || '',
        item_name: i.item_name || '',
        amount: i.total_amount ?? 0,
        compliant: i.compliant,
        feishu: g.feishu_final_status || '',  // 飞书终态 (2026-11-20 发票列表筛选)
        exported_at: i.exported_at || '',  // 最近导出时间 (导出流程回写, 2026-11-24)
        export_history: i.export_history || null,  // 导出历史时间数组 (末位=最近, 2026-11-24)
      }))
  ).sort((a, b) => (b.invoice_date || '9999').localeCompare(a.invoice_date || '9999'));
  // 发票类型筛选 (2026-11-23 七类: 专票/铁路客票/航空行程单/旅客运输服务/普票/车辆通行费; "全部"不过滤; 未识别类型仅在"全部"下显示)
  const filteredInvoices = flatInvoices.filter((r) => {
    if (invTypeFilter !== 'all' && r.invoice_class !== invTypeFilter) return false;
    // 发票号搜索 (2026-11-23): 子串匹配发票号码, 大小写不敏感
    if (invNoSearch) {
      const _q = invNoSearch.trim().toLowerCase();
      if (!_q || !(r.invoice_number || '').toLowerCase().includes(_q)) return false;
    }
    if (invFrom && (!r.invoice_date || r.invoice_date < invFrom)) return false;
    if (invTo && (!r.invoice_date || r.invoice_date > invTo)) return false;
    // 飞书状态筛选 (2026-11-20): 目前仅"已通过"(APPROVED), 置灰固定
    if (invFeishuFilter === 'approved' && r.feishu !== 'APPROVED') return false;
    // 只收 发票文件(pdf/ofd/xml), 不含单据及其他格式
    if (r.file_type !== 'invoice') return false;
    const _ext = (r.file_name || '').toLowerCase().split('.').pop() || '';
    if (!['pdf', 'ofd', 'xml'].includes(_ext)) return false;
    return true;
  });
  // 发票列表分页 (2026-11-19): 每页20条, 筛选/日期变化时回第一页
  const INV_PAGE_SIZE = 20;
  const invTotalPages = Math.max(1, Math.ceil(filteredInvoices.length / INV_PAGE_SIZE));
  const safeInvPage = Math.min(invPage, invTotalPages);
  const pagedInvoices = filteredInvoices.slice((safeInvPage - 1) * INV_PAGE_SIZE, safeInvPage * INV_PAGE_SIZE);
  useEffect(() => { setInvPage(1); setInvSelected(new Set()); }, [invTypeFilter, invFrom, invTo, invFeishuFilter, invNoSearch, selectedCompany, view]);
  // 导出 (2026-11-19): 只导当前筛选范围内的, 异步 zip
  // 后端打包: 合并总PDF(每份后插一行标注) + OFD/ 子目录 + XML/ 子目录 + 导出清单.txt;
  // 原件从飞书回源拉取 (本地缓存优先), 数量多时耗时长, 前端轮询进度, 完成后自动触发下载
  const [exportState, setExportState] = useState<{ taskId: string; progress: number; current: string; status: string } | null>(null);
  const [exportConfirm, setExportConfirm] = useState(false); // 导出二次确认弹框 (2026-11-24)
  const [invSelected, setInvSelected] = useState<Set<number>>(new Set()); // 批量选择导出 (2026-11-24)
  const toggleInvSel = (id: number) => setInvSelected((prev) => {
    const next = new Set(prev);
    if (next.has(id)) next.delete(id); else next.add(id);
    return next;
  });
  const toggleInvSelAll = () => setInvSelected((prev) =>
    prev.size && filteredInvoices.every((r) => prev.has(r.id)) ? new Set() : new Set(filteredInvoices.map((r) => r.id))
  );
  const startExport = async (ids?: number[]) => {
    if (exportState && exportState.status !== 'error') return; // 进行中防重复点
    const target = (ids && ids.length ? ids : filteredInvoices.map((r) => r.id));
    if (target.length === 0) return;
    try {
      const taskId = await startInvoiceExport(target);
      setExportState({ taskId, progress: 0, current: '排队中', status: 'pending' });
      const timer = window.setInterval(async () => {
        try {
          const st = await getInvoiceExportStatus(taskId);
          if (st.status === 'done') {
            window.clearInterval(timer);
            await downloadInvoiceExport(taskId, st.filename || '发票汇总.zip');
            setExportState(null);
            loadData(true); // 刷新列表, 导出状态列即时反映 (2026-11-24)
          } else if (st.status === 'error') {
            window.clearInterval(timer);
            const tail = st.errors && st.errors.length ? st.errors[st.errors.length - 1].slice(-100) : '';
            setExportState({ taskId, progress: st.progress, current: `导出失败${tail ? ' — ' + tail : ''}`, status: 'error' });
          } else {
            setExportState({ taskId, progress: st.progress, current: st.current, status: st.status });
          }
        } catch {
          window.clearInterval(timer);
          setExportState({ taskId, progress: 0, current: '进度查询失败, 请重试', status: 'error' });
        }
      }, 1500);
    } catch (e) {
      setExportState({ taskId: '', progress: 0, current: `导出启动失败: ${e instanceof Error ? e.message : String(e)}`, status: 'error' });
    }
  };

  // 单张发票下载 (2026-11-24): 单张下载也算一次导出 — 后端追加导出历史, 完成后刷新列表让"导出状态"绿对号即时反映
  const onDlSingle = async (r: any, fmt: 'pdf' | 'ofd' | 'xml') => {
    try {
      await downloadInvoiceFormat(r.id, fmt);
      loadData(true); // 重新拉取, 导出状态列(历史)即时更新
    } catch (e) {
      console.error('单张下载失败:', e);
    }
  };
  // 打开类目逐笔对账: 列表接口不含 category_reconcile, 首次点击按需拉详情并合并;
  // 结果内嵌在右侧"问题明细"区, 拉取后展开 crOpen
  const openCategoryReconcile = async () => {
    if (!selectedGroup) return;
    if (!selectedGroup.category_reconcile) {
      setCrLoading(true);
      try {
        const detail = await fetchApprovalDetail(selectedGroup.serial_number);
        setSelectedGroup({ ...selectedGroup, category_reconcile: detail.category_reconcile ?? null });
      } catch (e) {
        console.error('获取类目逐笔对账失败', e);
      } finally {
        setCrLoading(false);
      }
    }
    setCrOpen(true);
  };

  // 详情弹框按需拉取表单扩展字段 (2026-11-17 起附件原件已移除, 只拉 form_fields): 列表接口不含, 打开弹框且缺该字段时拉详情补齐;
  // dep 只跟 serial 走, 合并后不再重拉 (已带字段则直接跳过)
  useEffect(() => {
    const serial = selectedGroup?.serial_number;
    if (!serial) return;
    if (selectedGroup.form_fields) return;
    let cancelled = false;
    (async () => {
      try {
        const detail = await fetchApprovalDetail(serial);
        if (cancelled) return;
        setSelectedGroup((prev) =>
          prev && prev.serial_number === serial
            ? { ...prev, form_fields: detail.form_fields ?? prev.form_fields }
            : prev
        );
      } catch (e) {
        console.error('获取表单扩展字段失败', e);
      }
    })();
    return () => { cancelled = true; };
  }, [selectedGroup?.serial_number]);

  return (
    <div className="app-shell">
      {/* 左侧竖置菜单导航 (2026-11-09, 支持一键折叠) */}
      <aside className={sideCollapsed ? 'sidebar collapsed' : 'sidebar'}>
        <div className="side-brand">
          <span className="side-logo">🧩</span>
          <span className="side-brand-text">CEO助手</span>
          <button
            className="side-collapse-btn"
            title={sideCollapsed ? '展开菜单' : '折叠菜单'}
            onClick={toggleSide}
          >
            {sideCollapsed ? '»' : '«'}
          </button>
        </div>
        <div className="side-label">MENU</div>
        {/* 审批看板 → 部门二级菜单 (2026-11-24): 财务部处理 费用报销/差旅费报销; 采购/行政 预留 */}
        <button
          className="side-item side-group"
          title="审核看板"
          onClick={() => {
            if (sideCollapsed) { setSideCollapsed(false); setView('finance'); }
            else setBoardOpen((o) => !o);
          }}
        >
          <span className="side-icon">📊</span>
          <span className="side-text">审核看板</span>
          <span className="side-chev">{boardOpen ? '▾' : '▸'}</span>
        </button>
        {!sideCollapsed && boardOpen && (
          <div className="side-subgroup">
            {(['finance', 'procurement', 'admin'] as const).map((d) => (
              <button
                key={d}
                className={view === d ? 'side-item side-sub active' : 'side-item side-sub'}
                onClick={() => setView(d)}
              >
                <span className="side-icon">{d === 'finance' ? '💰' : d === 'procurement' ? '🛒' : '🗂'}</span>
                <span className="side-text">{DEPT_NAMES[d]}</span>
                <span className="side-badge">{approvals.filter((g) => DEPT_TYPES[d].includes(g.approval_type)).length}</span>
              </button>
            ))}
          </div>
        )}
        <button
          className={view === 'skills' ? 'side-item active' : 'side-item'}
          title="技能仓库"
          onClick={() => setView('skills')}
        >
          <span className="side-icon">🧩</span>
          <span className="side-text">技能仓库</span>
        </button>
        <div className="side-label">GENERAL</div>
        <div className="side-company">
          {selectedCompany ? (
            <>
              <div className="side-company-name">🏢 {selectedCompany}</div>
              <div className="side-company-sub">当前公司 · 实时同步</div>
            </>
          ) : (
            <div className="side-company-name">未选择公司</div>
          )}
        </div>
      </aside>

      <div className="app-main">
      {view === 'skills' ? (
        <SkillsView />
      ) : (
      <div className="app">
      <div className="header">
        <h1>📊 CEO助手 - 审批审核看板 · {DEPT_NAMES[view] || ''}</h1>
        <p>数据更新时间: {stats?.generated_at ? new Date(stats.generated_at).toLocaleString('zh-CN') : '-'}</p>
      </div>

      {/* 公司Tab切换 */}
      {companies.length >= 1 && (
        <div className="company-tabs">
          {companies.map((company, idx) => (
            <button
              key={idx}
              className={`company-tab ${selectedCompany === company.name ? 'active' : ''}`}
              onClick={() => setSelectedCompany(company.name)}
            >
              {company.name}
              <span className="tab-app-badge">{company.app_id_short}</span>
            </button>
          ))}
        </div>
      )}

      <div className="stats-container">
        <div className="stat-card">
          <div className="stat-value">{deptStats.total}</div>
          <div className="stat-label">审批单数</div>
        </div>
        <div className="stat-card success">
          <div className="stat-value">{deptStats.success}</div>
          <div className="stat-label">已通过</div>
        </div>
        <div className="stat-card failed">
          <div className="stat-value">{deptStats.failed}</div>
          <div className="stat-label">不通过</div>
        </div>
        <div className="stat-card">
          <div className="stat-value">¥{deptStats.amount.toFixed(2)}</div>
          <div className="stat-label">总金额</div>
        </div>
      </div>
      {/* 未接入部门的空态提示 (2026-11-24): 采购部/行政部 暂无审批类型 */}
      {deptTypes.length === 0 && (
        <div className="dept-banner">
          {view === 'procurement' ? '🛒' : '🗂'} {DEPT_NAMES[view]} 审核看板建设中 — 暂未接入审批类型，当前无待审数据
        </div>
      )}

      {/* 主区 Tab: 审批列表 / 发票列表 (2026-11-06) */}
      <div className="main-tabs">
        <button
          className={mainTab === 'approvals' ? 'main-tab active' : 'main-tab'}
          onClick={() => setMainTab('approvals')}
        >审批列表</button>
        <button
          className={mainTab === 'invoices' ? 'main-tab active' : 'main-tab'}
          onClick={() => setMainTab('invoices')}
        >发票列表</button>
      </div>

      {mainTab === 'approvals' && (
      <>
      <div className="filter-section">
        <select value={filter} onChange={(e) => setFilter(e.target.value as any)}>
          <option value="all">全部</option>
          <option value="success">已通过</option>
          <option value="failed">不通过</option>
        </select>
        <input
          type="text"
          placeholder="搜索审批编号 / 申请人 / 发票号..."
          value={search}
          onChange={(e) => setSearch(e.target.value)}
        />
      </div>

      <div className="approval-table-container">
        <table className="approval-table">
          <thead>
            <tr>
              <th>审批编号</th>
              <th>申请人</th>
              <th>审批类型</th>
              <th>发起时间</th>
              <th>审核时间</th>
              <th>预审状态</th>
              <th>飞书终态</th>
              <th>审批金额</th>
              <th>发票总额</th>
              <th>差额</th>
              <th>文件数</th>
              <th>有重复票</th>
              <th>备注</th>
              <th>操作</th>
            </tr>
          </thead>
          <tbody>
            {pagedApprovals.length === 0 && (
              <tr><td colSpan={14} className="inv-empty">暂无符合条件的审批单</td></tr>
            )}
            {pagedApprovals.map((group) => {
              const diff = group.approval_amount - group.total_invoice_amount;
              return (
                <tr key={group.serial_number + group.approval_id}>
                  <td>{group.serial_number}</td>
                  <td>{group.applicant_name}</td>
                  <td>{group.approval_type}</td>
                  <td>{formatDate(group.start_time)}</td>
                  <td>{group.audit_time || '-'}</td>
                  <td>
                    {/* 状态以数据库 status 字段为唯一口径 (发票合规 + 清单核对 + 金额差额) */}
                    {(() => {
                      const finalStatus = group.status === 'success' ? 'success' : 'failed';
                      return (
                        <span className={`status-badge ${finalStatus}`}>
                          {finalStatus === 'success' ? '✓ 通过' : '✗ 不通过'}
                        </span>
                      );
                    })()}
                  </td>
                  <td>
                    {group.feishu_final_status ? (() => {
                      const fs = group.feishu_final_status;
                      const cfg: Record<string, [string, string]> = {
                        APPROVED: ['success', '已通过'],
                        REJECTED: ['failed', '已驳回'],
                        TERMINATED: ['failed', '已终止'],
                        CANCELED: ['warning', '已撤销'],
                        PENDING: ['warning', '审批中'],
                      };
                      const [cls, label] = cfg[fs] || ['warning', fs];
                      return <span className={`status-badge ${cls}`}>{label}</span>;
                    })() : (
                      <span className="inv-empty">未记录</span>
                    )}
                  </td>
                  <td>¥{group.approval_amount.toFixed(2)}</td>
                  <td>¥{group.total_invoice_amount.toFixed(2)}</td>
                  <td className={diff > 0 ? 'amount-diff' : ''}>
                    {diff > 0 ? `¥${diff.toFixed(2)}` : '-'}
                  </td>
                  <td>{group.file_count}</td>
                  <td>
                    {group.has_duplicate_invoice ? (
                      <span 
                        className="duplicate-badge" 
                        title={group.has_duplicate_invoice}
                        onClick={() => {
                          const serials = group.has_duplicate_invoice!.split(',');
                          const relatedGroups = approvals.filter(a => serials.includes(a.serial_number));
                          setDuplicateGroups(relatedGroups);
                          setShowDuplicateDialog(true);
                        }}
                      >
                        {group.has_duplicate_invoice}
                      </span>
                    ) : (
                      <span className="no-duplicate">-</span>
                    )}
                  </td>
                  <td>
                    {group.issues && group.issues.length > 0 ? (
                      <span 
                        className="remark-cell" 
                        title={group.issues.map(i => {
                          const msgs = typeof i.messages === 'string' ? JSON.parse(i.messages) : i.messages;
                          return `${i.type}: ${msgs.join(', ')}`;
                        }).join('\n')}
                      >
                        {group.issues.map(i => {
                          const msgs = typeof i.messages === 'string' ? JSON.parse(i.messages) : i.messages;
                          return `${i.type}: ${msgs.join(', ')}`;
                        }).join('; ')}
                      </span>
                    ) : (
                      <span className="no-remark">-</span>
                    )}
                  </td>
                  <td className="actions">
                    <button className="btn-detail" onClick={() => setSelectedGroup(group)}>详情</button>
                    <button className="btn-link" onClick={() => copyLink(group)}>链接</button>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
        {/* 审批列表分页器 (2026-11-19) */}
        {filteredApprovals.length > PAGE_SIZE && (
          <div className="pagination-bar">
            <button className="pg-btn" disabled={safeApprovalPage <= 1} onClick={() => setApprovalPage(safeApprovalPage - 1)}>« 上一页</button>
            <span className="pg-info">{safeApprovalPage} / {approvalTotalPages} 页 · 共 {filteredApprovals.length} 条</span>
            <button className="pg-btn" disabled={safeApprovalPage >= approvalTotalPages} onClick={() => setApprovalPage(safeApprovalPage + 1)}>下一页 »</button>
          </div>
        )}
      </div>
      </>
      )}

      {mainTab === 'invoices' && (
      <>
      {/* 发票列表筛选区: 发票类型 + 开票日期 + 飞书状态 + 导出(只导筛选范围内) */}
      <div className="filter-section invoice-filter">
        <span className="inv-date-label">发票类型</span>
        <select value={invTypeFilter} onChange={(e) => setInvTypeFilter(e.target.value as any)}>
          <option value="all">全部</option>
          <option value="增值税专用发票">增值税专用发票</option>
          <option value="铁路电子客票">铁路电子客票</option>
          <option value="航空运输电子客票行程单">航空运输电子客票行程单</option>
          <option value="旅客运输服务">旅客运输服务</option>
          <option value="增值税普通发票">增值税普通发票</option>
          <option value="车辆通行费">车辆通行费</option>
          <option value="其他">其他</option>
        </select>
        <span className="inv-date-label">开票日期</span>
        <input type="date" value={invFrom} onChange={(e) => setInvFrom(e.target.value)} />
        <span className="inv-date-sep">~</span>
        <input type="date" value={invTo} onChange={(e) => setInvTo(e.target.value)} />
        <span className="inv-date-label">飞书状态</span>
        <select value={invFeishuFilter} onChange={(e) => setInvFeishuFilter(e.target.value as any)}
                disabled title="目前仅支持『已通过』筛选 (只显示审批列表内飞书终态为已通过的发票文件)">
          <option value="approved">已通过</option>
        </select>
        <span className="inv-date-label">发票号</span>
        <input type="text" value={invNoSearch} onChange={(e) => setInvNoSearch(e.target.value)}
               placeholder="输入发票号码(可部分匹配)" className="inv-no-search" />
        <button className={`btn-export-invoices${invSelected.size === 0 ? ' is-empty' : ''}`}
                onClick={() => { if (invSelected.size > 0) setExportConfirm(true); }}
                disabled={(!!exportState && exportState.status !== 'error') || invSelected.size === 0}>
          {exportState && exportState.status !== 'error'
            ? `导出中 ${exportState.progress}%…`
            : invSelected.size > 0
              ? `导出选中 (${invSelected.size}条)`
              : `请选择要导出的发票`}
        </button>
      </div>
      {/* 导出二次确认弹框 (2026-11-24) */}
      {exportConfirm && (
        <div className="dialog-overlay">
          <div className="export-modal">
            <div className="export-modal-title">确认导出发票</div>
            <div className="export-modal-body">
              <p>将导出 <b>当前列表勾选的 {invSelected.size}</b> 条发票文件（与上方筛选器/发票号搜索作用范围一致）。</p>
              <p>后端异步打包为 zip：7 类发票按类合并 PDF + 1 份 Excel 汇总，原件从飞书回源拉取，数量较多时可能需要几分钟。</p>
              <p className="export-modal-hint">完成后自动触发下载，无需保持页面操作。</p>
            </div>
            <div className="export-modal-actions">
              <button className="export-modal-btn cancel" onClick={() => setExportConfirm(false)}>取消</button>
              <button className="export-modal-btn ok" onClick={() => { setExportConfirm(false); startExport(Array.from(invSelected)); }}>开始导出</button>
            </div>
          </div>
        </div>
      )}
      {/* 导出进度圆环弹框 (2026-11-24, 替代原内联进度条) */}
      {exportState && (
        <div className="dialog-overlay">
          <div className="export-modal">
            <div className="export-modal-title">{exportState.status === 'error' ? '发票导出失败' : '发票导出中'}</div>
            <div className="export-ring-wrap">
              <svg className="export-ring" viewBox="0 0 120 120">
                <circle cx="60" cy="60" r="52" fill="none" stroke="#eef1f6" strokeWidth="10" />
                <circle cx="60" cy="60" r="52" fill="none"
                  stroke={exportState.status === 'error' ? '#d9363c' : '#1890ff'} strokeWidth="10" strokeLinecap="round"
                  strokeDasharray={`${(Math.min(100, Math.max(0, exportState.progress)) / 100) * 326.73} 326.73`}
                  transform="rotate(-90 60 60)" />
              </svg>
              <div className={`export-ring-pct ${exportState.status === 'error' ? 'export-ring-pct-error' : ''}`}>{exportState.progress}%</div>
            </div>
            <div className={`export-modal-body ${exportState.status === 'error' ? 'export-error-text' : ''}`}>
              <p>{exportState.current}</p>
            </div>
            <div className="export-modal-actions">
              {exportState.status === 'error' && (
                <button className="export-modal-btn cancel" onClick={() => setExportState(null)}>关闭</button>
              )}
            </div>
          </div>
        </div>
      )}

      <div className="approval-table-container">
        <table className="approval-table invoice-table">
          <thead>
            <tr>
              <th className="inv-chk">
                <input type="checkbox" aria-label="全选"
                  checked={filteredInvoices.length > 0 && filteredInvoices.every((r) => invSelected.has(r.id))}
                  onChange={toggleInvSelAll} />
              </th>
              <th>审批编号</th>
              <th>申请人</th>
              <th>类型</th>
              <th>发票类型</th>
              <th>文件名</th>
              <th>发票号</th>
              <th>开票日期</th>
              <th>购买方</th>
              <th>销售方</th>
              <th>金额(元)</th>
              <th>合规</th>
              <th>导出状态</th>
              <th>下载</th>
            </tr>
          </thead>
          <tbody>
            {filteredInvoices.length === 0 && (
              <tr><td colSpan={14} className="inv-empty">该筛选范围内无发票文件</td></tr>
            )}
            {pagedInvoices.map((r, idx) => (
              <tr key={`${r.serial}-${r.invoice_number}-${idx}`} onClick={() => { /* 占位, 后续可接深链 */ }}>
                <td className="inv-chk">
                  <input type="checkbox" aria-label="选择" checked={invSelected.has(r.id)}
                    onChange={() => toggleInvSel(r.id)} onClick={(e) => e.stopPropagation()} />
                </td>
                <td className="mono">{r.serial}</td>
                <td>{r.applicant}</td>
                <td>{r.approval_type}</td>
                <td title={r.invoice_type || ''}>{r.invoice_class || '-'}</td>
                <td title={r.file_name} className="inv-file">{r.file_name}</td>
                <td className="mono">{r.invoice_number || '-'}</td>
                <td>{r.invoice_date || '-'}</td>
                <td title={r.buyer} className="inv-buyer">{r.buyer || '-'}</td>
                <td title={r.seller} className="inv-seller">{r.seller || '-'}</td>
                <td className="num">{r.amount ? r.amount.toFixed(2) : '-'}</td>
                <td>{r.compliant ? <span className="ok-mark">合规</span> : <span className="bad-mark">不合规</span>}</td>
                {/* 导出状态 (2026-11-24): 未导出"-", 已导出绿色对号; 悬停显示全部导出历史(多条) */}
                <td className="inv-exported">
                  {(() => {
                    const hist: string[] = (r.export_history && r.export_history.length)
                      ? r.export_history
                      : (r.exported_at ? [r.exported_at] : []);
                    if (!hist.length) return <span className="inv-exported-none">-</span>;
                    return (
                      <span className="inv-exported-wrap">
                        <span className="inv-exported-mark">✓</span>
                        <span className="inv-exported-tip" role="tooltip">
                          <span className="inv-exported-tip-head">导出记录 · 共{hist.length}次</span>
                          {hist.map((t, i) => (
                            <span key={i} className="inv-exported-tip-line">{i + 1}. {t}</span>
                          ))}
                        </span>
                      </span>
                    );
                  })()}
                </td>
                {/* 单张下载 (2026-11-19): PDF/OFD 取原件, XML 从 OFD 内嵌提取; 按原件格式条件显示; 下载即视为一次导出 (2026-11-24) */}
                <td className="inv-dl">
                  {(() => { const f = dlFormats(r);
                    return (
                      <span className="inv-dl-btns">
                        {f.pdf && <button className="inv-dl-btn" title="下载PDF原件" onClick={(e) => { e.stopPropagation(); onDlSingle(r, 'pdf'); }}>PDF</button>}
                        {f.ofd && <button className="inv-dl-btn" title="下载OFD原件" onClick={(e) => { e.stopPropagation(); onDlSingle(r, 'ofd'); }}>OFD</button>}
                        {f.xml && <button className="inv-dl-btn" title="下载OFD内嵌XML(打包zip)" onClick={(e) => { e.stopPropagation(); onDlSingle(r, 'xml'); }}>XML</button>}
                        {!f.pdf && !f.ofd && <span className="inv-dl-none">-</span>}
                      </span>
                    ); })()}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {/* 发票列表分页器 (2026-11-19) */}
        {filteredInvoices.length > INV_PAGE_SIZE && (
          <div className="pagination-bar">
            <button className="pg-btn" disabled={safeInvPage <= 1} onClick={() => setInvPage(safeInvPage - 1)}>« 上一页</button>
            <span className="pg-info">{safeInvPage} / {invTotalPages} 页 · 共 {filteredInvoices.length} 条</span>
            <button className="pg-btn" disabled={safeInvPage >= invTotalPages} onClick={() => setInvPage(safeInvPage + 1)}>下一页 »</button>
          </div>
        )}
      </div>
      </>
      )}

      {/* 重复票弹框 */}
      {showDuplicateDialog && (
        <div className="dialog-overlay" onClick={() => setShowDuplicateDialog(false)}>
          <div className="dialog" onClick={() => setShowDuplicateDialog(false)}>
            <div className="dialog-content" onClick={(e) => e.stopPropagation()}>
              <table className="duplicate-table">
                <thead>
                  <tr>
                    <th>审批编号</th>
                    <th>申请人</th>
                    <th>金额</th>
                    <th>状态</th>
                    <th>发起时间</th>
                  </tr>
                </thead>
                <tbody>
                  {duplicateGroups.map((g) => (
                    <tr key={g.serial_number}>
                      <td>{g.serial_number}</td>
                      <td>{g.applicant_name}</td>
                      <td>¥{g.approval_amount.toFixed(2)}</td>
                      <td>
                        <span className={`status-badge status-${g.status}`}>
                          {g.status === 'success' ? '通过' : '不通过'}
                        </span>
                      </td>
                      <td>{new Date(g.start_time).toLocaleString()}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        </div>
      )}

      {/* 类目逐笔对账: 结果内嵌在右侧"问题明细"区, 不再单独弹框 (2026-11-03 改为内嵌) */}

      {selectedGroup && (
        <div className="dialog" onClick={() => setSelectedGroup(null)}>
          <div className="dialog-content" onClick={(e) => e.stopPropagation()}>
            <div className="dialog-header">
              <h3>审批详情 - {selectedGroup.serial_number}</h3>
              <button className="close-btn" onClick={() => setSelectedGroup(null)}>×</button>
            </div>
            <div className="dialog-body">
              {/* 左侧：基本信息 + Tab切换（发票明细/行程单汇总/报销清单） */}
              <div className="dialog-left">
                {/* 基本信息 */}
                <div className="detail-section">
                  <h4>基本信息</h4>
                  <div className="detail-grid">
                    <div className="detail-item">
                      <label>审批编号：</label>
                      <span>{selectedGroup.serial_number}</span>
                    </div>
                    <div className="detail-item">
                      <label>审批类型：</label>
                      <span>{selectedGroup.approval_type}</span>
                    </div>
                    <div className="detail-item">
                      <label>发起人员：</label>
                      <span>{selectedGroup.applicant_name}</span>
                    </div>
                    <div className="detail-item">
                      <label>发起时间：</label>
                      <span>{formatDate(selectedGroup.start_time)}</span>
                    </div>
                    <div className="detail-item">
                      <label>审批金额：</label>
                      <span className="amount">¥{selectedGroup.approval_amount.toFixed(2)}</span>
                    </div>
                    <div className="detail-item">
                      <label>发票总额：</label>
                      <span className="amount">¥{selectedGroup.total_invoice_amount.toFixed(2)}</span>
                    </div>
                    <div className="detail-item">
                      <label>差额：</label>
                      <span className={selectedGroup.approval_amount > selectedGroup.total_invoice_amount ? 'amount-diff' : 'amount'}>
                        ¥{(selectedGroup.approval_amount - selectedGroup.total_invoice_amount).toFixed(2)}
                      </span>
                    </div>
                    <div className="detail-item">
                      <label>审核结果：</label>
                      <span className={selectedGroup.status === 'failed' ? 'status-warning' : 'status-success'}>
                        {selectedGroup.status === 'failed' ? (
                          selectedGroup.issues && selectedGroup.issues.length > 0 
                            ? `${selectedGroup.issues.length}项问题`
                            : '不通过'
                        ) : '通过'}
                      </span>
                    </div>
                    {selectedGroup.trip_destinations && (
                      <div className="detail-item">
                        <label>出差地點：</label>
                        <span>{selectedGroup.trip_destinations}</span>
                      </div>
                    )}
                    {selectedGroup.travel_dates && selectedGroup.travel_dates.start && (
                      <div className="detail-item">
                        <label>出差日期：</label>
                        <span>{selectedGroup.travel_dates.start} ~ {selectedGroup.travel_dates.end}</span>
                      </div>
                    )}
                    {/* 表单扩展字段 (2026-11-17): 领款人名单/发放形式/起止时间/时长/补助/住宿/车费等, 空则不渲染 */}
                    {selectedGroup.form_fields && selectedGroup.form_fields.length > 0 && selectedGroup.form_fields.map((ff) => (
                      <div key={ff.label} className="detail-item">
                        <label>{ff.label}：</label>
                        <span>{ff.value}</span>
                      </div>
                    ))}
                  </div>
                </div>

                {/* Tab切换区 */}
                {(selectedGroup.invoices && selectedGroup.invoices.length > 0) ||
                 (selectedGroup.trip_summaries && selectedGroup.trip_summaries.length > 0) ||
                 (selectedGroup.lists && selectedGroup.lists.length > 0) ? (
                  <div className="detail-section tab-section">
                    <div className="dialog-tabs">
                      {invoicesOnly.length > 0 && (
                        <button
                          className={`dialog-tab ${activeDialogTab === '发票明细' ? 'active' : ''}`}
                          onClick={() => setActiveDialogTab('发票明细')}
                        >
                          发票明细 ({invoicesOnly.length})
                        </button>
                      )}
                      {selectedGroup.trip_summaries && selectedGroup.trip_summaries.length > 0 && (
                        <button
                          className={`dialog-tab ${activeDialogTab === '行程单汇总' ? 'active' : ''}`}
                          onClick={() => setActiveDialogTab('行程单汇总')}
                        >
                          行程单汇总 ({selectedGroup.trip_summaries.length})
                        </button>
                      )}
                      {selectedGroup.lists && selectedGroup.lists.length > 0 && (
                        <button
                          className={`dialog-tab ${activeDialogTab === '报销清单' ? 'active' : ''}`}
                          onClick={() => setActiveDialogTab('报销清单')}
                        >
                          报销清单 ({selectedGroup.lists.length})
                        </button>
                      )}
                      {receiptsOnly.length > 0 && (
                        <button
                          className={`dialog-tab ${activeDialogTab === '其他单据' ? 'active' : ''}`}
                          onClick={() => setActiveDialogTab('其他单据')}
                        >
                          其他单据 ({receiptsOnly.length})
                        </button>
                      )}
                    </div>

                    {/* 发票明细Tab (仅真发票，行程单在"行程单汇总"tab展示) */}
                    {activeDialogTab === '发票明细' && invoicesOnly.length > 0 && (
                      <div className="tab-content">
                        <table className="invoice-list-table">
                          <thead>
                            <tr>
                              <th className="col-file">文件名</th>
                              <th className="col-number">发票号</th>
                              <th className="col-date">日期</th>
                              <th className="col-buyer">购买方</th>
                              <th className="col-amount">金额</th>
                              <th className="col-status">状态</th>
                              <th className="col-detail">详情</th>
                            </tr>
                          </thead>
                          <tbody>
                            {invoicesOnly.map((invoice, idx) => {
                              const isExpanded = expandedInvoices.has(idx);
                              return (
                                <>
                                  <tr
                                    key={idx}
                                    className={`invoice-row ${invoice.compliant ? 'row-compliant' : 'row-warning'} ${expandedInvoices.has(idx) ? 'invoice-row-expanded' : ''}`}
                                  >
                                    <td className="col-file">
                                      <span className="file-name">
                                        {invoice.file_name.replace(/_发票\d+$/, '')}
                                        {invoice.file_name.includes('_发票') && (
                                          <span className="invoice-sub">（第{invoice.file_name.match(/_发票(\d+)/)?.[1] || '1'}张）</span>
                                        )}
                                      </span>
                                    </td>
                                    <td className="col-number mono">{invoice.invoice_number || '-'}</td>
                                    <td className="col-date">{invoice.invoice_date || '-'}</td>
                                    <td className="col-buyer">{invoice.buyer_name || '-'}</td>
                                    <td className="col-amount">¥{invoice.total_amount.toFixed(2)}</td>
                                    <td className="col-status">
                                      <span className={`status-badge ${invoice.compliant ? 'success' : 'failed'}`}>
                                        {invoice.compliant ? '✓ 合规' : '✗ 不合规'}
                                      </span>
                                    </td>
                                    <td className="col-detail">
                                      <button
                                        className="detail-toggle-btn"
                                        onClick={(e) => {
                                          e.stopPropagation();
                                          const newExpanded = new Set(expandedInvoices);
                                          if (newExpanded.has(idx)) {
                                            newExpanded.delete(idx);
                                          } else {
                                            newExpanded.add(idx);
                                          }
                                          setExpandedInvoices(newExpanded);
                                        }}
                                      >
                                        {isExpanded ? '▲' : '▼'}
                                      </button>
                                    </td>
                                  </tr>
                                  {isExpanded && (
                                    <tr key={`detail-${idx}`} className="invoice-detail-row">
                                      <td colSpan={7}>
                                        <div className="invoice-detail-content">
                                          <div className="invoice-detail-section">
                                            <h5>📋 发票基本信息 & 审核结果</h5>
                                            <div className="invoice-detail-grid">
                                              <div className="detail-column">
                                                <div className="detail-item"><span className="detail-label">文件：</span><span className="detail-value">{invoice.file_name}</span></div>
                                                <div className="detail-item"><span className="detail-label">发票号：</span><span className="detail-value mono">{invoice.invoice_number || '无'}</span></div>
                                                <div className="detail-item"><span className="detail-label">开票日期：</span><span className="detail-value">{invoice.invoice_date || '无'}</span></div>
                                                <div className="detail-item"><span className="detail-label">购买方：</span><span className="detail-value">{invoice.buyer_name || '无'}</span></div>
                                                {invoice.buyer_credit_code && <div className="detail-item"><span className="detail-label">购买方税号：</span><span className="detail-value mono">{invoice.buyer_credit_code}</span></div>}
                                                <div className="detail-item"><span className="detail-label">销售方：</span><span className="detail-value">{invoice.seller_name || '无'}</span></div>
                                                {invoice.seller_credit_code && <div className="detail-item"><span className="detail-label">销售方税号：</span><span className="detail-value mono">{invoice.seller_credit_code}</span></div>}
                                                <div className="detail-item"><span className="detail-label">金额：</span><span className="detail-value amount">¥{invoice.total_amount.toFixed(2)}</span></div>
                                                <div className="detail-item"><span className="detail-label">类型：</span><span className="detail-value">{invoice.invoice_type || '电子发票'}</span></div>
                                                {invoice.item_name && <div className="detail-item"><span className="detail-label">购买项目：</span><span className="detail-value">{invoice.item_name}</span></div>}
                                              </div>
                                              <div className="detail-column audit-column">
                                                <div className="audit-results">
                                                  {invoice.buyer_check && (
                                                    <div className={`audit-item ${invoice.buyer_check.passed ? 'pass' : 'fail'}`}>
                                                      <div className="audit-row">
                                                        <span className="audit-icon">{invoice.buyer_check.passed ? '✓' : '⚠️'}</span>
                                                        <span className="audit-label">购买方合规：</span>
                                                        <span className="audit-message">{invoice.buyer_check.message}</span>
                                                      </div>
                                                      {invoice.buyer_check.detail && (
                                                        <div className="audit-detail">{invoice.buyer_check.detail}</div>
                                                      )}
                                                    </div>
                                                  )}
                                                  {invoice.items_check && (
                                                    <div className={`audit-item ${invoice.items_check.passed ? 'pass' : 'fail'}`}>
                                                      <div className="audit-row">
                                                        <span className="audit-icon">{invoice.items_check.passed ? '✓' : '⚠️'}</span>
                                                        <span className="audit-label">项目合规：</span>
                                                        <span className="audit-message">{invoice.items_check.message}</span>
                                                      </div>
                                                      {invoice.items_check.detail && (
                                                        <div className="audit-detail">{invoice.items_check.detail}</div>
                                                      )}
                                                    </div>
                                                  )}
                                                  {(['taxi_trip_check', 'car_rental_check', 'transport_check', 'passenger_check', 'travel_check', 'accommodation_check'] as const).map((ck) => {
                                                    const chk = invoice[ck];
                                                    if (!chk || !chk.message) return null;
                                                    const isSkip = chk.passed === null;
                                                    return (
                                                      <div key={ck} className={`audit-item ${isSkip ? 'pass' : chk.passed ? 'pass' : 'fail'}`}>
                                                        <div className="audit-row">
                                                          <span className="audit-icon">{chk.passed ? '✓' : isSkip ? '—' : '⚠️'}</span>
                                                          <span className="audit-label">{chk.label || ck}：</span>
                                                          <span className="audit-message">{chk.message}</span>
                                                        </div>
                                                      </div>
                                                    );
                                                  })}
                                                </div>
                                              </div>
                                            </div>
                                          </div>
                                          {invoice.ocr_text && (
                                            <div className="invoice-detail-section">
                                              <h5>📄 OCR识别原文</h5>
                                              <pre className="ocr-text">{invoice.ocr_text}</pre>
                                            </div>
                                          )}
                                        </div>
                                      </td>
                                    </tr>
                                  )}
                                </>
                              );
                            })}
                          </tbody>
                        </table>
                        <div className="total-row">
                          <span className="total-label">合计：</span>
                          <span className="total-amount">¥{selectedGroup.total_invoice_amount.toFixed(2)}</span>
                        </div>
                      </div>
                    )}

                    {/* 行程单汇总Tab */}
                    {activeDialogTab === '行程单汇总' && selectedGroup.trip_summaries && selectedGroup.trip_summaries.length > 0 && (
                      <div className="tab-content">
                        <table className="trip-summary-table">
                          <thead>
                            <tr>
                              <th className="col-type">类型</th>
                              <th className="col-pages">页码</th>
                              <th className="col-trips">行程数</th>
                              <th className="col-amount">金额</th>
                              <th className="col-dates">日期范围</th>
                              <th className="col-detail">明细</th>
                            </tr>
                          </thead>
                          <tbody>
                            {selectedGroup.trip_summaries.map((trip, idx) => {
                              const isExpanded = expandedTrips.has(idx);
                              return (
                                <>
                                  <tr
                                    key={idx}
                                    className={`trip-row ${trip.details && trip.details.length > 0 ? 'clickable' : ''}`}
                                    onClick={() => {
                                      if (trip.details && trip.details.length > 0) {
                                        const newExpanded = new Set(expandedTrips);
                                        if (isExpanded) {
                                          newExpanded.delete(idx);
                                        } else {
                                          newExpanded.add(idx);
                                        }
                                        setExpandedTrips(newExpanded);
                                      }
                                    }}
                                  >
                                    <td className="col-type">
                                      <span className={`trip-type-badge ${(trip.trip_type || trip.type || '行程单').toLowerCase()}`}>
                                        {trip.trip_type || trip.type || '行程单'}
                                      </span>
                                    </td>
                                    <td className="col-pages">{(trip.page || trip.pages || 1)}页</td>
                                    <td className="col-trips">{trip.trips ?? '-'}</td>
                                    <td className="col-amount">¥{(trip.total_amount ?? 0).toFixed(2)}</td>
                                    <td className="col-dates">
                                      {trip.start_date || '-'} 至 {trip.end_date || '-'}
                                    </td>
                                    <td className="col-detail">
                                      {trip.details && trip.details.length > 0 ? (
                                        <span className="expand-icon">{isExpanded ? '▲' : '▼'}</span>
                                      ) : (
                                        <span className="no-detail">-</span>
                                      )}
                                    </td>
                                  </tr>
                                  {isExpanded && trip.details && (
                                    <tr key={`detail-${idx}`} className="trip-detail-row">
                                      <td colSpan={6}>
                                        <div className="trip-details-container">
                                          <div className="trip-details-header">
                                            <span className="trip-details-title">行程明细 - {trip.trip_type}</span>
                                            <span className="trip-details-count">{trip.details.length}笔</span>
                                          </div>
                                          <table className="trip-detail-table">
                                            <thead>
                                              <tr>
                                                <th className="col-seq">序号</th>
                                                <th className="col-type">车型</th>
                                                <th className="col-time">时间</th>
                                                <th className="col-city">城市</th>
                                                <th className="col-from">起点</th>
                                                <th className="col-to">终点</th>
                                                <th className="col-distance">里程</th>
                                                <th className="col-amount">金额</th>
                                              </tr>
                                            </thead>
                                            <tbody>
                                              {trip.details.map((d, i) => (
                                                <tr key={i} className="detail-row">
                                                  <td className="col-seq">{d.seq}</td>
                                                  <td className="col-type">{d.type}</td>
                                                  <td className="col-time">{d.time}</td>
                                                  <td className="col-city">{d.city}</td>
                                                  <td className="col-from">{d.from}</td>
                                                  <td className="col-to">{d.to}</td>
                                                  <td className="col-distance">{d.distance}km</td>
                                                  <td className="col-amount">¥{d.amount}</td>
                                                </tr>
                                              ))}
                                            </tbody>
                                          </table>
                                        </div>
                                      </td>
                                    </tr>
                                  )}
                                </>
                              );
                            })}
                          </tbody>
                        </table>
                      </div>
                    )}

                    {/* 报销清单Tab */}
                    {activeDialogTab === '报销清单' && selectedGroup.lists && selectedGroup.lists.length > 0 && (
                      <div className="tab-content">
                        {selectedGroup.lists.map((list, idx) => (
                          <div key={idx} className="list-file">
                            <div className="file-header">
                              <span className="file-name">{list.file_name}</span>
                              <span className="file-type">📋 清单文件</span>
                            </div>

                            {selectedGroup.list_verify && (
                              <div className="list-verify-result">
                                <div className="verify-summary">
                                  <div className={`verify-status ${selectedGroup.list_verify.match ? 'match' : 'mismatch'}`}>
                                    {selectedGroup.list_verify.match ? '✓ 核对通过' : '✗ 核对不通过'}
                                  </div>
                                  <div className="verify-stats">
                                    <span>清单项目: {selectedGroup.list_verify.list_items_count}个</span>
                                    <span>发票数量: {selectedGroup.list_verify.invoice_count}张</span>
                                    <span>清单总额: ¥{selectedGroup.list_verify.list_total.toFixed(2)}</span>
                                    <span>发票总额: ¥{selectedGroup.list_verify.invoice_total.toFixed(2)}</span>
                                  </div>
                                  {selectedGroup.approval_type === '差旅费报销' && (
                                    <button
                                      className="cr-open-btn"
                                      onClick={openCategoryReconcile}
                                      disabled={crLoading}
                                    >
                                      {crLoading ? '加载中…' : '类目逐笔对账'}
                                      {selectedGroup.category_reconcile &&
                                        selectedGroup.category_reconcile.summary.failed_categories.length > 0 &&
                                        ` (${selectedGroup.category_reconcile.summary.failed_categories.join('、')} 有差异)`}
                                    </button>
                                  )}
                                </div>

                                {selectedGroup.approval_type === '差旅费报销' && (
                                  selectedGroup.list_verify?.travel_allowance_check ? (
                                    <div className="allowance-check">
                                      <div className={`allowance-status ${selectedGroup.list_verify.travel_allowance_check.passed ? 'pass' : 'warn'}`}>
                                        {selectedGroup.list_verify.travel_allowance_check.passed ? '✓' : '⚠️'}
                                        <span>{selectedGroup.list_verify.travel_allowance_check.message}</span>
                                      </div>
                                      {selectedGroup.list_verify.travel_dates && selectedGroup.list_verify.travel_dates.length > 0 && (
                                        <div className="allowance-dates">
                                          出差日期（{selectedGroup.list_verify.travel_dates.length}天）: {selectedGroup.list_verify.travel_dates.sort().join(', ')}
                                        </div>
                                      )}
                                    </div>
                                  ) : selectedGroup.allowance_check ? (
                                    <div className="allowance-check">
                                      <div className={`allowance-status ${selectedGroup.allowance_check.checks?.t8?.passed ? 'pass' : 'warn'}`}>
                                        {selectedGroup.allowance_check.checks?.t8?.passed ? '✓' : '⚠️'}
                                        <span>{selectedGroup.allowance_check.checks?.t8?.message || '差旅补助合规'}</span>
                                      </div>
                                    </div>
                                  ) : null
                                )}

                                {selectedGroup.approval_type === '差旅费报销' && selectedGroup.rental_check && (
                                  <div className={`rental-check ${selectedGroup.rental_check.status === 'pass' ? 'pass' : 'fail'}`}>
                                    <div className="rental-check-header">
                                      <span className="rental-check-icon">{selectedGroup.rental_check.status === 'pass' ? '✓' : '⚠️'}</span>
                                      <span className="rental-check-message">{selectedGroup.rental_check.message}</span>
                                    </div>
                                    {selectedGroup.rental_check.details?.issues && selectedGroup.rental_check.details.issues.length > 0 && (
                                      <div className="rental-check-issues">
                                        {selectedGroup.rental_check.details.issues.map((issue, i) => (
                                          <div key={i} className="rental-issue">⚠️ {issue}</div>
                                        ))}
                                    {selectedGroup.rental_check.details?.warnings && selectedGroup.rental_check.details.warnings.length > 0 && (
                                      <div className="rental-check-warnings">
                                        {selectedGroup.rental_check.details.warnings.map((w, i) => (
                                          <div key={i} className="rental-warning">📌 {w}</div>
                                        ))}
                                      </div>
                                    )}
                                      </div>
                                    )}
                                  </div>
                                )}

                                {selectedGroup.list_verify?.issues && selectedGroup.list_verify.issues.length > 0 && (
                                  <div className="verify-issues">
                                    {selectedGroup.list_verify.issues.map((issue, i) => (
                                      <div key={i} className="verify-issue">⚠️ {issue.message}</div>
                                    ))}
                                  </div>
                                )}

                                {selectedGroup.list_verify?.list_items && selectedGroup.list_verify.list_items.length > 0 && (
                                  <div className="list-items-detail">
                                    <h5>清单消费明细</h5>
                                    <table className="list-items-table">
                                      <thead>
                                        <tr>
                                          <th>日期</th>
                                          <th>行程</th>
                                          <th>类型</th>
                                          <th>车费</th>
                                          <th>住宿</th>
                                          <th>补贴</th>
                                          <th>其他车费</th>
                                          <th>其他</th>
                                          <th>备注</th>
                                          <th>小计</th>
                                        </tr>
                                      </thead>
                                      <tbody>
                                        {selectedGroup.list_verify.list_items.map((item, i) => (
                                          <tr key={i}>
                                            <td>{item.date || '-'}</td>
                                            <td>{item.from && item.to ? `${item.from}-${item.to}` : '-'}</td>
                                            <td>{item.type || '-'}</td>
                                            <td>{item.transport > 0 ? `¥${item.transport.toFixed(2)}` : '-'}</td>
                                            <td>{item.hotel > 0 ? `¥${item.hotel.toFixed(2)}` : '-'}</td>
                                            <td>{(item.subsidy ?? 0) > 0 ? `¥${(item.subsidy ?? 0).toFixed(2)}` : '-'}</td>
                                            <td>{item.other_transport > 0 ? `¥${item.other_transport.toFixed(2)}` : '-'}</td>
                                            <td>{item.other > 0 ? `¥${item.other.toFixed(2)}` : '-'}</td>
                                            <td>{item.remark || '-'}</td>
                                            <td className="list-subtotal">{item.total > 0 ? `¥${item.total.toFixed(2)}` : '-'}</td>
                                          </tr>
                                        ))}
                                      </tbody>
                                    </table>
                                  </div>
                                )}
                              </div>
                            )}
                          </div>
                        ))}
                      </div>
                    )}

                    {/* 其他单据Tab（仅在该tab激活时渲染，避免内容泄漏到其他tab） */}
                    {activeDialogTab === '其他单据' && receiptsOnly.length > 0 && (
                      <div className="tab-content">
                        <table className="invoice-list-table">
                          <thead>
                            <tr>
                              <th className="col-file">文件名</th>
                              <th className="col-number">单据类型</th>
                              <th className="col-date">日期</th>
                              <th className="col-amount">金额</th>
                              <th className="col-status">状态</th>
                              <th className="col-detail">详情</th>
                            </tr>
                          </thead>
                          <tbody>
                            {receiptsOnly.map((receipt, idx) => {
                              const isExpanded = expandedInvoices.has(idx + 1000);
                              return (
                                <>
                                  <tr
                                    key={idx}
                                    className={`invoice-row ${receipt.compliant ? 'row-compliant' : 'row-warning'} ${isExpanded ? 'invoice-row-expanded' : ''}`}
                                  >
                                    <td className="col-file">
                                      <span className="file-name">{receipt.file_name}</span>
                                    </td>
                                    <td className="col-number">{receiptTypeName(receipt.file_name, receipt.invoice_type)}</td>
                                    <td className="col-date">{receipt.invoice_date || '-'}</td>
                                    <td className="col-amount">¥{receipt.total_amount.toFixed(2)}</td>
                                    <td className="col-status">
                                      <span className={`status-badge ${receipt.compliant ? 'success' : 'failed'}`}>
                                        {receipt.compliant ? '✓ 合规' : '✗ 不合规'}
                                      </span>
                                    </td>
                                    <td className="col-detail">
                                      <button
                                        className="detail-toggle-btn"
                                        onClick={(e) => {
                                          e.stopPropagation();
                                          const newExpanded = new Set(expandedInvoices);
                                          const expandedKey = idx + 1000;
                                          if (newExpanded.has(expandedKey)) {
                                            newExpanded.delete(expandedKey);
                                          } else {
                                            newExpanded.add(expandedKey);
                                          }
                                          setExpandedInvoices(newExpanded);
                                        }}
                                      >
                                        {isExpanded ? '▲' : '▼'}
                                      </button>
                                    </td>
                                  </tr>
                                  {isExpanded && (
                                    <tr key={`detail-${idx}`} className="invoice-detail-row">
                                      <td colSpan={6}>
                                        <div className="invoice-detail-content">
                                          <div className="invoice-detail-section">
                                            <h5>📋 单据详情</h5>
                                            <div className="invoice-detail-grid">
                                              <div className="detail-column">
                                                <div className="detail-item"><span className="detail-label">文件名：</span><span className="detail-value">{receipt.file_name}</span></div>
                                                <div className="detail-item"><span className="detail-label">单据类型：</span><span className="detail-value">{receiptTypeName(receipt.file_name, receipt.invoice_type)}</span></div>
                                                <div className="detail-item"><span className="detail-label">单据号：</span><span className="detail-value mono">{receipt.invoice_number || '无'}</span></div>
                                                <div className="detail-item"><span className="detail-label">日期：</span><span className="detail-value">{receipt.invoice_date || '无'}</span></div>
                                                <div className="detail-item"><span className="detail-label">购买方：</span><span className="detail-value">{receipt.buyer_name || '无'}</span></div>
                                                <div className="detail-item"><span className="detail-label">金额：</span><span className="detail-value amount">¥{receipt.total_amount.toFixed(2)}</span></div>
                                              </div>
                                              <div className="detail-column audit-column">
                                                <div className="audit-results">
                                                  {receipt.items_check && (
                                                    <div className={`audit-item ${receipt.items_check.passed ? 'pass' : 'fail'}`}>
                                                      <div className="audit-row">
                                                        <span className="audit-icon">{receipt.items_check.passed ? '✓' : '⚠️'}</span>
                                                        <span className="audit-label">单据合规：</span>
                                                        <span className="audit-message">{(receipt.items_check.message || '').replace('发票项目合规', '单据项目合规')}</span>
                                                      </div>
                                                    </div>
                                                  )}
                                                  {receipt.taxi_trip_check && (
                                                    <div className={`audit-item ${receipt.taxi_trip_check.passed ? 'pass' : 'fail'}`}>
                                                      <div className="audit-row">
                                                        <span className="audit-icon">{receipt.taxi_trip_check.passed ? '✓' : '⚠️'}</span>
                                                        <span className="audit-label">行程核对：</span>
                                                        <span className="audit-message">{receipt.taxi_trip_check.message}</span>
                                                      </div>
                                                    </div>
                                                  )}
                                                  {receipt.car_rental_check && (
                                                    <div className={`audit-item ${receipt.car_rental_check.passed ? 'pass' : 'fail'}`}>
                                                      <div className="audit-row">
                                                        <span className="audit-icon">{receipt.car_rental_check.passed ? '✓' : '⚠️'}</span>
                                                        <span className="audit-label">租车核对：</span>
                                                        <span className="audit-message">{receipt.car_rental_check.message}</span>
                                                      </div>
                                                    </div>
                                                  )}
                                                  {receipt.accommodation_check && (
                                                    <div className={`audit-item ${receipt.accommodation_check.passed ? 'pass' : 'fail'}`}>
                                                      <div className="audit-row">
                                                        <span className="audit-icon">{receipt.accommodation_check.passed ? '✓' : '⚠️'}</span>
                                                        <span className="audit-label">住宿核对：</span>
                                                        <span className="audit-message">{receipt.accommodation_check.message}</span>
                                                      </div>
                                                    </div>
                                                  )}
                                                </div>
                                              </div>
                                            </div>
                                          </div>
                                        </div>
                                      </td>
                                    </tr>
                                  )}
                                </>
                              );
                            })}
                          </tbody>
                        </table>
                      </div>
                    )}
                  </div>
                ) : null}

              </div>

              {/* 右侧：结论、问题明细、合规审核依据 */}
              <div className="dialog-right">
                {/* 审核结论 */}
                <div className="detail-section conclusion-section">
                  <h4>审核结论</h4>
                  <div className={`conclusion-box ${selectedGroup.status === 'failed' ? 'conclusion-failed' : 'conclusion-pass'}`}>
                    <div className="conclusion-icon">
                      {selectedGroup.status === 'failed' ? '⚠️' : '✓'}
                    </div>
                    <div className="conclusion-content">
                      <div className="conclusion-title">
                        {selectedGroup.status === 'failed' ? '存在不合规项' : '审核通过'}
                      </div>
                      <div className="conclusion-detail">
                        共{invoicesOnly.length}张发票，总金额¥{selectedGroup.total_invoice_amount.toFixed(2)}，
                        {selectedGroup.status === 'failed' ? (
                          selectedGroup.issues && selectedGroup.issues.length > 0 
                            ? `${selectedGroup.issues.length}项问题需关注`
                            : '审核不通过'
                        ) : '符合规范'}
                      </div>
                    </div>
                  </div>

                  {selectedGroup.list_verify && (
                    <div className="list-verify-summary">
                      <div className="verify-title">清单核对结果</div>
                      <div className={`verify-status ${selectedGroup.list_verify.match ? 'match' : 'mismatch'}`}>
                        {selectedGroup.list_verify.match ? '✓ 核对通过' : '✗ 核对不通过'}
                      </div>
                      <div className="verify-detail">
                        <div>清单¥{selectedGroup.list_verify.list_total.toFixed(2)} vs 发票¥{selectedGroup.list_verify.invoice_total.toFixed(2)}</div>
                        {selectedGroup.list_verify?.travel_allowance && selectedGroup.list_verify.travel_allowance > 0 && (
                          <div className="verify-allowance-note">
                            清单总额含出差补助¥{selectedGroup.list_verify.travel_allowance.toFixed(2)}，扣除后¥{(selectedGroup.list_verify.comparable_amount ?? selectedGroup.list_verify.invoice_total).toFixed(2)}与发票总额核对
                          </div>
                        )}
                        {selectedGroup.list_verify?.approval_amount && selectedGroup.list_verify.approval_amount > 0 && (
                          <div className={selectedGroup.list_verify.approval_match ? 'verify-ok' : 'verify-error'}>
                            发票¥{selectedGroup.list_verify.invoice_total.toFixed(2)} vs 审批¥{selectedGroup.list_verify.approval_amount.toFixed(2)}
                            {selectedGroup.list_verify.approval_match ? ' ✓' : ' ✗'}
                          </div>
                        )}
                        {!selectedGroup.list_verify.match && (
                          <div className="verify-diff">差额¥{(selectedGroup.list_verify.list_total - selectedGroup.list_verify.invoice_total).toFixed(2)}</div>
                        )}
                      </div>
                    </div>
                  )}
                </div>

                {/* 问题明细 */}
                <div className={`detail-section issues-section ${selectedGroup.issues?.length ? '' : 'empty'}`}>
                  <h4>问题明细 ({(selectedGroup.issues?.length ?? 0) + (crOpen && selectedGroup.category_reconcile ? 1 : 0)}项)</h4>
                  <div className="issues-list">
                    {selectedGroup.issues?.map((issue, idx) => (
                      <div key={idx} className="issue-row">
                        <span className="issue-file">{issue.file}</span>
                        <span className="issue-type">[{issue.type}]</span>
                        {(Array.isArray(issue.messages) ? issue.messages : [issue.messages]).filter(m => m).map((msg, i) => (
                          <span key={i} className="issue-messages">⚠️ {msg}</span>
                        ))}
                      </div>
                    ))}

                    {/* 类目逐笔对账结果: 内嵌在问题明细区 (2026-11-03 改为内嵌) */}
                    {crOpen && selectedGroup.category_reconcile && (
                      <div className="issue-row cr-embedded">
                        <span className="issue-file">类目逐笔对账</span>
                        <span className="issue-type">[{selectedGroup.category_reconcile.summary.passed ? '总额一致' : '总额不一致'}]</span>

                        <div className="cr-summary-bar cr-embedded-bar">
                          <span>配对成功 {selectedGroup.category_reconcile.summary.total_matched} 笔</span>
                          {selectedGroup.category_reconcile.summary.failed_categories.length > 0 ? (
                            <span className="cr-bad">
                              差异类目: {selectedGroup.category_reconcile.summary.failed_categories.join('、')}
                            </span>
                          ) : (
                            <span className="cr-ok">各类目总额一致</span>
                          )}
                          <span className="cr-hint">匹配口径: 先按「金额+起止地名」配对, 再按「金额」配对; 剩余记为单边</span>
                        </div>

                        {selectedGroup.category_reconcile.categories.map((c: CategoryReconcileItem) => {
                          const open = crExpanded.has(c.category) || (!c.passed && !crExpanded.has('_' + c.category));
                          const toggle = () => {
                            const next = new Set(crExpanded);
                            const key = c.passed ? c.category : '_' + c.category;
                            if (next.has(key)) next.delete(key); else next.add(key);
                            setCrExpanded(next);
                          };
                          return (
                            <div key={c.category} className={`cr-cat ${c.passed ? 'ok' : 'bad'}`}>
                              <div className="cr-cat-head" onClick={toggle}>
                                <span className="cr-cat-name">{c.category}</span>
                                <span className={`cr-badge ${c.passed ? 'ok' : 'bad'}`}>
                                  {c.passed ? '总额一致' : `差 ${c.diff > 0 ? '+' : ''}${c.diff.toFixed(2)}`}
                                </span>
                                <span className="cr-nums">
                                  清单 ¥{c.list.toFixed(2)} / 发票 ¥{c.invoice.toFixed(2)}
                                  {c.form !== null ? ` / 表单 ¥${c.form.toFixed(2)}` : ''}
                                  <em className="cr-cnt">（清单 {c.list_count} 行 vs 发票 {c.invoice_count} 张）</em>
                                </span>
                                <span className="cr-toggle">{open ? '收起' : '展开'}</span>
                              </div>

                              {open && (
                                <div className="cr-body">
                                  <div className="cr-note">{c.note}</div>
                                  {c.list_only.length === 0 && c.invoice_only.length === 0 && (
                                    <div className="cr-allmatch">全部逐笔配对成功</div>
                                  )}
                                  {c.list_only.length > 0 && (
                                    <div className="cr-col">
                                      <div className="cr-col-title bad">
                                        只在清单（{c.list_only_total} 笔，合计 ¥{c.list_only_sum.toFixed(2)}）
                                      </div>
                                      {c.list_only.map((r, i) => (
                                        <div className="cr-row" key={`l${i}`}>
                                          <span className="cr-amt">¥{r.amount.toFixed(2)}</span>
                                          <span className="cr-desc">{r.desc}</span>
                                        </div>
                                      ))}
                                    </div>
                                  )}
                                  {c.invoice_only.length > 0 && (
                                    <div className="cr-col">
                                      <div className="cr-col-title warn">
                                        只在发票（{c.invoice_only_total} 笔，合计 ¥{c.invoice_only_sum.toFixed(2)}）
                                      </div>
                                      {c.invoice_only.map((r, i) => (
                                        <div className="cr-row" key={`v${i}`}>
                                          <span className="cr-amt">¥{r.amount.toFixed(2)}</span>
                                          <span className="cr-desc">
                                            {r.desc}
                                            {r.invoice_number ? <em className="cr-invno">{r.invoice_number}</em> : null}
                                          </span>
                                        </div>
                                      ))}
                                    </div>
                                  )}
                                </div>
                              )}
                            </div>
                          );
                        })}

                        <button
                          className="cr-close"
                          onClick={() => setCrOpen(false)}
                          type="button"
                        >
                          收起类目逐笔对账
                        </button>
                      </div>
                    )}
                  </div>
                </div>

                {/* 合规审核依据 - Tab切换 */}
                <div className="detail-section compliance-section">
                  <h4>合规审核依据</h4>
                  <div className="compliance-tabs">
                    <button
                      className={`compliance-tab ${activeComplianceTab === '发票合规' ? 'active' : ''}`}
                      onClick={() => setActiveComplianceTab('发票合规')}
                    >
                      发票合规
                    </button>
                    <button
                      className={`compliance-tab ${activeComplianceTab === '金额核对' ? 'active' : ''}`}
                      onClick={() => setActiveComplianceTab('金额核对')}
                    >
                      金额核对
                    </button>
                    {selectedGroup.approval_type === '差旅费报销' && (
                      <>
                        <button
                          className={`compliance-tab ${activeComplianceTab === '行程票据' ? 'active' : ''}`}
                          onClick={() => setActiveComplianceTab('行程票据')}
                        >
                          行程票据
                        </button>
                        <button
                          className={`compliance-tab ${activeComplianceTab === '住宿核查' ? 'active' : ''}`}
                          onClick={() => setActiveComplianceTab('住宿核查')}
                        >
                          住宿核查
                        </button>
                        <button
                          className={`compliance-tab ${activeComplianceTab === '补助核对' ? 'active' : ''}`}
                          onClick={() => setActiveComplianceTab('补助核对')}
                        >
                          补助核对
                        </button>
                      </>
                    )}
                  </div>
                  <div className="compliance-content">
                    {activeComplianceTab === '发票合规' && (
                      <div className="compliance-grid">
                        <div className="compliance-item">
                          <span className="compliance-label">购买方检查：</span>
                          <span className="compliance-desc">验证发票购买方名称、税号是否与企业信息一致</span>
                        </div>
                        <div className="compliance-item">
                          <span className="compliance-label">项目检查：</span>
                          <span className="compliance-desc">验证发票项目内容是否完整、合理，能否推断具体消费内容</span>
                        </div>
                        <div className="compliance-item">
                          <span className="compliance-label">文件分类：</span>
                          <span className="compliance-desc">区分发票文件(.pdf/.jpg)与清单文件(.xlsx)，清单文件仅作汇总参考</span>
                        </div>
                      </div>
                    )}
                    {activeComplianceTab === '金额核对' && (
                      <div className="compliance-grid">
                        <div className="compliance-item">
                          <span className="compliance-label">总额核对：</span>
                          <span className="compliance-desc">比对发票总额与审批申请金额是否一致，允许误差±0.01元</span>
                        </div>
                        <div className="compliance-item">
                          <span className="compliance-label">清单核对：</span>
                          <span className="compliance-desc">清单消费明细总额与发票总额是否匹配</span>
                        </div>
                      </div>
                    )}
                    {activeComplianceTab === '行程票据' && selectedGroup.approval_type === '差旅费报销' && (
                      <div className="compliance-grid">
                        <div className="compliance-item">
                          <span className="compliance-label">行程票据：</span>
                          <span className="compliance-desc">高铁/机票/出租车等行程交通票据应覆盖出差往返交通需求，起讫地点应与出差目的地匹配，票据日期应在出差日期范围内，乘客应与出差人员一致</span>
                        </div>
                        <div className="compliance-item">
                          <span className="compliance-label">出行人核对：</span>
                          <span className="compliance-desc">行程交通票据/旅客运输发票的出行人应与报销出差人员一致</span>
                        </div>
                        <div className="compliance-item">
                          <span className="compliance-label">打车行程单：</span>
                          <span className="compliance-desc">打车行程单金额应与发票金额一致；上下车地点应在出差地城区内；打车日期应在出差日期范围内；乘客信息应与报销人一致</span>
                        </div>
                        <div className="compliance-item">
                          <span className="compliance-label">租车结算单：</span>
                          <span className="compliance-desc">租车结算单金额应与发票金额一致；租车地点应与出差目的地一致；租期不应超出出差日期范围</span>
                        </div>
                      </div>
                    )}
                    {activeComplianceTab === '住宿核查' && selectedGroup.approval_type === '差旅费报销' && (
                      <div className="compliance-grid">
                        <div className="compliance-item">
                          <span className="compliance-label">住宿费标准：</span>
                          <span className="compliance-desc">单日住宿费按目的地城市限额：一线(京沪穗深)≤600元，二线(杭宁蓉汉西渝)≤500元，三线≤400元，四线及以下≤300元</span>
                        </div>
                        <div className="compliance-item">
                          <span className="compliance-label">入住日期：</span>
                          <span className="compliance-desc">入住信息单的入住/离店时间应在出差日期范围内，不应超期</span>
                        </div>
                        <div className="compliance-item">
                          <span className="compliance-label">住宿地点：</span>
                          <span className="compliance-desc">住宿发票销售方所在城市应与出差目的地一致</span>
                        </div>
                        <div className="compliance-item">
                          <span className="compliance-label">住宿人员：</span>
                          <span className="compliance-desc">入住信息单登记住宿人应与出差/报销人员一致</span>
                        </div>
                      </div>
                    )}
                    {activeComplianceTab === '补助核对' && selectedGroup.approval_type === '差旅费报销' && (
                      <div className="compliance-grid">
                        <div className="compliance-item">
                          <span className="compliance-label">出差补助费：</span>
                          <span className="compliance-desc">补助标准 80元/天，金额=出差天数×80（天数从清单日期提取）；审批表单含"出差补助费"字段时核对实际申报值</span>
                        </div>
                      </div>
                    )}
                  </div>
                </div>
              </div>
            </div>
          </div>
        </div>
      )}
      </div>
      )}
      </div>
    </div>
  );
}

export default App;
