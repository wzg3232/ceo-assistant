import { useState, useEffect, useMemo } from 'react';
import { marked } from 'marked';
import {
  fetchSkills, fetchSkillDetail, createSkill, updateSkillContent,
  deleteSkill, uploadSkillZip, DEPARTMENTS, type SkillMeta,
} from './api';

/* 复制文本: 优先剪贴板 API, 失败(非HTTPS/旧浏览器)降级 execCommand */
async function copyText(text: string): Promise<boolean> {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    try {
      const ta = document.createElement('textarea');
      ta.value = text;
      ta.style.position = 'fixed';
      ta.style.opacity = '0';
      document.body.appendChild(ta);
      ta.select();
      const ok = document.execCommand('copy');
      document.body.removeChild(ta);
      return ok;
    } catch {
      return false;
    }
  }
}

function SkillsView() {
  const [skills, setSkills] = useState<SkillMeta[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [search, setSearch] = useState('');
  const [activeTag, setActiveTag] = useState('');
  const [activeDept, setActiveDept] = useState('');

  // 详情弹窗
  const [detail, setDetail] = useState<SkillMeta | null>(null);
  const [showRaw, setShowRaw] = useState(false);
  // 编辑
  const [editing, setEditing] = useState(false);
  const [editContent, setEditContent] = useState('');
  const [editDept, setEditDept] = useState('通用');
  // 上传
  const [showUpload, setShowUpload] = useState(false);
  const [pasteContent, setPasteContent] = useState('');
  const [author, setAuthor] = useState('');
  const [upDept, setUpDept] = useState('通用');
  const [zipFile, setZipFile] = useState<File | null>(null);
  const [toast, setToast] = useState('');

  const showToast = (msg: string) => {
    setToast(msg);
    setTimeout(() => setToast(''), 2600);
  };

  const load = async () => {
    setLoading(true);
    setError('');
    try {
      setSkills(await fetchSkills());
    } catch {
      setError('无法加载技能数据，请确认后端服务已启动');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { load(); }, []);

  const allTags = useMemo(() => {
    const s = new Set<string>();
    skills.forEach((k) => k.tags.forEach((t) => s.add(t)));
    return Array.from(s);
  }, [skills]);

  const deptCounts = useMemo(() => {
    const m = new Map<string, number>();
    skills.forEach((k) => {
      const d = DEPARTMENTS.includes(k.department as any) ? k.department : '通用';
      m.set(d, (m.get(d) || 0) + 1);
    });
    return m;
  }, [skills]);

  const filtered = useMemo(() => {
    const kw = search.trim().toLowerCase();
    return skills.filter((k) => {
      const dept = DEPARTMENTS.includes(k.department as any) ? k.department : '通用';
      if (activeDept && dept !== activeDept) return false;
      if (activeTag && !k.tags.includes(activeTag)) return false;
      if (!kw) return true;
      return (
        k.name.toLowerCase().includes(kw) ||
        k.description.toLowerCase().includes(kw) ||
        k.id.toLowerCase().includes(kw) ||
        k.tags.some((t) => t.toLowerCase().includes(kw))
      );
    });
  }, [skills, search, activeTag, activeDept]);

  const openDetail = async (id: string) => {
    try {
      const d = await fetchSkillDetail(id);
      setDetail(d);
      setShowRaw(false);
      setEditing(false);
    } catch {
      showToast('加载详情失败');
    }
  };

  const openEdit = () => {
    if (!detail) return;
    setEditContent(detail.content || '');
    setEditDept(DEPARTMENTS.includes(detail.department as any) ? detail.department : '通用');
    setEditing(true);
  };

  const saveEdit = async () => {
    if (!detail) return;
    try {
      await updateSkillContent(detail.id, editContent, author || detail.authorName, editDept);
      setEditing(false);
      showToast('✅ 已保存，旧版本已备份');
      await load();
      await openDetail(detail.id);
    } catch {
      showToast('保存失败');
    }
  };

  const doDelete = async () => {
    if (!detail) return;
    if (!window.confirm(`确定删除技能「${detail.name}」？此操作不可恢复。`)) return;
    try {
      await deleteSkill(detail.id);
      setDetail(null);
      showToast('已删除');
      await load();
    } catch {
      showToast('删除失败');
    }
  };

  const doCreatePaste = async () => {
    if (!pasteContent.trim()) { showToast('请先粘贴 SKILL.md 内容'); return; }
    try {
      const r = await createSkill(pasteContent, author || '匿名', upDept);
      setShowUpload(false);
      setPasteContent('');
      showToast(`✅ 已创建: ${r.id}`);
      await load();
      await openDetail(r.id);
    } catch (e: any) {
      showToast(e?.response?.data?.detail || '创建失败');
    }
  };

  const doUploadZip = async () => {
    if (!zipFile) { showToast('请先选择 zip 文件'); return; }
    try {
      const r = await uploadSkillZip(zipFile, author || '匿名', upDept);
      setShowUpload(false);
      setZipFile(null);
      showToast(`✅ 已导入: ${r.id}`);
      await load();
      await openDetail(r.id);
    } catch (e: any) {
      showToast(e?.response?.data?.detail || '上传失败');
    }
  };

  const copyInstall = async (oneLiner: string) => {
    const ok = await copyText(oneLiner);
    showToast(ok ? '📋 安装口令已复制，粘贴给任意 agent 即可' : '复制失败，请手动选择文本复制');
  };

  const renderMarkdown = (content: string) => {
    try {
      return { __html: marked.parse(content || '') as string };
    } catch {
      return { __html: `<pre>${content}</pre>` };
    }
  };

  return (
    <div className="skills-view">
      <div className="skills-toolbar">
        <input
          className="skills-search"
          type="text"
          placeholder="搜索技能名称 / 描述 / 标签…"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
        />
        <button className="skills-upload-btn" onClick={() => setShowUpload(true)}>⬆ 上传 Skill</button>
      </div>

      {/* 部门分类栏 */}
      <div className="skills-depts">
        <button
          className={activeDept === '' ? 'skills-dept active' : 'skills-dept'}
          onClick={() => setActiveDept('')}
        >全部 <i>{skills.length}</i></button>
        {DEPARTMENTS.map((d) => (
          <button
            key={d}
            className={activeDept === d ? 'skills-dept active' : 'skills-dept'}
            onClick={() => setActiveDept(activeDept === d ? '' : d)}
          >{d} <i>{deptCounts.get(d) || 0}</i></button>
        ))}
      </div>

      {allTags.length > 0 && (
        <div className="skills-tags">
          <button
            className={activeTag === '' ? 'skills-tag active' : 'skills-tag'}
            onClick={() => setActiveTag('')}
          >全部</button>
          {allTags.map((t) => (
            <button
              key={t}
              className={activeTag === t ? 'skills-tag active' : 'skills-tag'}
              onClick={() => setActiveTag(t)}
            >{t}</button>
          ))}
        </div>
      )}

      {loading && <div className="skills-status">加载中…</div>}
      {error && <div className="skills-status error">{error}</div>}
      {!loading && !error && filtered.length === 0 && (
        <div className="skills-status">暂无技能，点右上角「上传 Skill」添加第一个吧</div>
      )}

      <div className="skills-grid">
        {filtered.map((k) => (
          <div key={k.id} className="skill-card" onClick={() => openDetail(k.id)}>
            <div className="skill-card-head">
              <span className="skill-icon">🧩</span>
              <span className="skill-name">{k.name}</span>
              <span className="skill-dept-badge">{DEPARTMENTS.includes(k.department as any) ? k.department : '通用'}</span>
            </div>
            <p className="skill-desc">{k.description || '（无描述）'}</p>
            <div className="skill-card-foot">
              <span className="skill-author">👤 {k.authorName || k.author}</span>
              <span className="skill-dl">⬇ {k.downloads}</span>
            </div>
            {k.tags.length > 0 && (
              <div className="skill-card-tags">
                {k.tags.map((t) => <span key={t} className="skill-tag-mini">{t}</span>)}
              </div>
            )}
          </div>
        ))}
      </div>

      {/* 详情弹窗 */}
      {detail && !editing && (
        <div className="skills-modal-mask" onClick={(e) => { if (e.target === e.currentTarget) setDetail(null); }}>
          <div className="skills-modal">
            <div className="skills-install-banner">
              <div className="skills-install-text">
                <strong>⚡ 一句话安装</strong>
                <code>{detail.oneLiner}</code>
              </div>
              <div className="skills-install-actions">
                <button className="sk-btn primary" onClick={() => copyInstall(detail.oneLiner)}>复制口令</button>
                <a className="sk-btn" href={`/api/skills/${detail.id}/download`}>⬇ 下载 zip</a>
              </div>
            </div>
            <div className="skills-detail-head">
              <h2>{detail.name}</h2>
              <div className="skills-detail-meta">
                <span className="skill-dept-badge">{DEPARTMENTS.includes(detail.department as any) ? detail.department : '通用'}</span>
                {' '}作者: {detail.authorName || detail.author}
                {detail.updatedBy && detail.updatedBy !== detail.authorName && ` · 最后编辑: ${detail.updatedBy}`}
                {detail.updatedAt && ` · 更新于 ${new Date(detail.updatedAt).toLocaleString('zh-CN')}`}
              </div>
            </div>
            <div className="skills-detail-actions">
              <button className="sk-btn" onClick={openEdit}>✏️ 在线编辑</button>
              <button className="sk-btn ghost" onClick={() => setShowRaw(!showRaw)}>{showRaw ? '渲染预览' : '查看源码'}</button>
              <button className="sk-btn danger-ghost" onClick={doDelete}>删除</button>
              <button className="sk-btn ghost close" onClick={() => setDetail(null)}>关闭</button>
            </div>
            {showRaw ? (
              <pre className="skills-raw">{detail.content}</pre>
            ) : (
              <div className="skills-preview" dangerouslySetInnerHTML={renderMarkdown(detail.content || '')} />
            )}
          </div>
        </div>
      )}

      {/* 编辑弹窗 */}
      {detail && editing && (
        <div className="skills-modal-mask" onClick={(e) => { if (e.target === e.currentTarget) setEditing(false); }}>
          <div className="skills-modal">
            <h2>✏️ 编辑「{detail.name}」</h2>
            <p className="skills-edit-hint">修改 SKILL.md 内容（frontmatter 与正文均可），保存后旧版本自动备份。</p>
            <div className="skills-dept-row">
              <label>所属部门：</label>
              <select className="skills-dept-select" value={editDept} onChange={(e) => setEditDept(e.target.value)}>
                {DEPARTMENTS.map((d) => <option key={d} value={d}>{d}</option>)}
              </select>
            </div>
            <textarea
              className="skills-editor"
              value={editContent}
              onChange={(e) => setEditContent(e.target.value)}
              spellCheck={false}
            />
            <div className="skills-detail-actions">
              <button className="sk-btn primary" onClick={saveEdit}>保存</button>
              <button className="sk-btn ghost" onClick={() => setEditing(false)}>取消</button>
            </div>
          </div>
        </div>
      )}

      {/* 上传弹窗 */}
      {showUpload && (
        <div className="skills-modal-mask" onClick={(e) => { if (e.target === e.currentTarget) setShowUpload(false); }}>
          <div className="skills-modal narrow">
            <h2>⬆ 上传 Skill</h2>
            <div className="skills-dept-row">
              <label>所属部门：</label>
              <select className="skills-dept-select" value={upDept} onChange={(e) => setUpDept(e.target.value)}>
                {DEPARTMENTS.map((d) => <option key={d} value={d}>{d}</option>)}
              </select>
            </div>
            <input
              className="skills-input"
              type="text"
              placeholder="你的署名（可选，默认匿名）"
              value={author}
              onChange={(e) => setAuthor(e.target.value)}
            />
            <div className="skills-upload-section">
              <label className="skills-section-title">方式一：上传 zip 包</label>
              <input
                type="file"
                accept=".zip"
                onChange={(e) => setZipFile(e.target.files?.[0] || null)}
              />
              <button className="sk-btn primary" onClick={doUploadZip} disabled={!zipFile}>导入 zip</button>
            </div>
            <div className="skills-upload-section">
              <label className="skills-section-title">方式二：粘贴 SKILL.md 文本</label>
              <textarea
                className="skills-paste"
                placeholder={'---\nname: my-skill\ndescription: 技能描述\n---\n\n# 技能内容…'}
                value={pasteContent}
                onChange={(e) => setPasteContent(e.target.value)}
                spellCheck={false}
              />
              <button className="sk-btn primary" onClick={doCreatePaste}>创建技能</button>
            </div>
            <div className="skills-detail-actions">
              <button className="sk-btn ghost" onClick={() => setShowUpload(false)}>关闭</button>
            </div>
          </div>
        </div>
      )}

      {toast && <div className="skills-toast">{toast}</div>}
    </div>
  );
}

export default SkillsView;
