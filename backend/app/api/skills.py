"""CEO助手 - 技能仓库 API (SkillHub, 2026-11-09)

技能数据存 Postgres skills 表, 与审批看板同一套服务。
功能: 列表/详情/创建(SKILL.md文本或zip)/在线编辑/删除/zip下载/一句话安装manifest。
首次访问时自动从旧 SkillHub 数据目录播种(skill-hub 项目)。
"""
import io
import json
import re
import zipfile
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException, UploadFile, File
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import func, text

from app.database import SessionLocal, engine
from app.models import Base, Skill

router = APIRouter(prefix="/api/skills", tags=["skills"])

# 部门分类(2026-11-10)
DEPARTMENTS = ['财务', '行政', '采购', '电力交易', '投资', '通用', '年度工作计划']


def _norm_department(d: Optional[str], frontmatter: dict = None) -> str:
    """归一化部门: 显式传参 > frontmatter > 默认通用; 不在名单内归为通用"""
    raw = (d or '').strip() or (frontmatter or {}).get('department', '').strip()
    return raw if raw in DEPARTMENTS else '通用'

# 旧 SkillHub 数据目录(播种来源)
LEGACY_DATA_DIR = Path(r"C:/Users/admin/WorkBuddy/2026-11-09-11-25-38/skill-hub/data/skills")

_schema_ready = False


def ensure_schema():
    """幂等建表(仅本路由的表); 已存在的表自动补缺失列"""
    global _schema_ready
    if not _schema_ready:
        Base.metadata.create_all(bind=engine, tables=[Skill.__table__])
        # 迁移: 老表补 department 列 (2026-11-10 部门分类)
        with engine.connect() as c:
            cols = [r[1] for r in c.execute(text("PRAGMA table_info(skills)"))] \
                if engine.dialect.name == 'sqlite' else \
                [r[0] for r in c.execute(text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_name='skills'"))]
            if 'department' not in cols:
                c.execute(text("ALTER TABLE skills ADD COLUMN department VARCHAR(50) NOT NULL DEFAULT '通用'"))
                c.commit()
                print("[skills] 已迁移: skills 表新增 department 列")
        _schema_ready = True


def slugify(name: str) -> str:
    slug = re.sub(r'[^a-z0-9]+', '-', (name or '').lower()).strip('-')
    return slug or 'skill'


def parse_frontmatter(content: str) -> dict:
    """解析 SKILL.md 的 YAML frontmatter (简单 key: value 形式)"""
    meta = {}
    m = re.match(r'^---\s*\n(.*?)\n---\s*\n?', content, re.S)
    body = content
    if m:
        body = content[m.end():]
        for line in m.group(1).splitlines():
            kv = re.match(r'^(\w[\w-]*):\s*(.*)$', line.strip())
            if kv:
                meta[kv.group(1)] = kv.group(2).strip().strip('"\'')
    return {'meta': meta, 'body': body}


def build_one_liner(skill_id: str, origin: str = '') -> str:
    base = origin.rstrip('/') or 'http://localhost:8002'
    return (
        f'请帮我安装技能「{skill_id}」：从 {base}/api/skills/{skill_id}/download 下载 zip，'
        f'解压到 ~/.workbuddy/skills/{skill_id}/，并确认 SKILL.md 存在'
    )


def seed_from_legacy():
    """从旧 skill-hub 目录播种(仅当表为空)"""
    db = SessionLocal()
    try:
        if db.query(func.count(Skill.id)).scalar() > 0:
            return
        if not LEGACY_DATA_DIR.exists():
            return
        for skill_dir in LEGACY_DATA_DIR.iterdir():
            if not skill_dir.is_dir() or skill_dir.name.startswith('.'):
                continue
            md = skill_dir / 'SKILL.md'
            if not md.exists():
                continue
            content = md.read_text(encoding='utf-8')
            parsed = parse_frontmatter(content)
            meta_file = skill_dir / 'meta.json'
            author, author_name = '匿名', ''
            if meta_file.exists():
                try:
                    m = json.loads(meta_file.read_text(encoding='utf-8'))
                    author = m.get('author') or '匿名'
                    author_name = m.get('authorName') or ''
                except Exception:
                    pass
            name = parsed['meta'].get('name') or skill_dir.name
            row = Skill(
                id=skill_dir.name,
                name=name,
                description=parsed['meta'].get('description', ''),
                tags=parsed['meta'].get('tags', ''),
                author=author,
                author_name=author_name,
                content=content,
            )
            db.merge(row)
        db.commit()
        print(f"[skills] 已从 {LEGACY_DATA_DIR} 播种技能数据")
    except Exception as e:
        db.rollback()
        print(f"[skills] 播种失败(不影响主服务): {e}")
    finally:
        db.close()


@router.get("")
def list_skills(origin: Optional[str] = None):
    ensure_schema()
    seed_from_legacy()
    db = SessionLocal()
    try:
        rows = db.query(Skill).order_by(Skill.updated_at.desc()).all()
        return [
            {**r.to_dict(), 'oneLiner': build_one_liner(r.id, origin or '')}
            for r in rows
        ]
    finally:
        db.close()


@router.get("/{skill_id}")
def get_skill(skill_id: str, origin: Optional[str] = None):
    ensure_schema()
    db = SessionLocal()
    try:
        row = db.get(Skill, skill_id)
        if not row:
            raise HTTPException(404, "skill 不存在")
        d = row.to_dict(with_content=True)
        d['oneLiner'] = build_one_liner(skill_id, origin or '')
        return d
    finally:
        db.close()


class SkillCreate(BaseModel):
    content: str
    author: str = '匿名'
    id: Optional[str] = None
    department: Optional[str] = None


@router.post("/create")
def create_skill(payload: SkillCreate):
    ensure_schema()
    parsed = parse_frontmatter(payload.content)
    meta = parsed['meta']
    name = meta.get('name') or '未命名技能'
    sid = slugify(payload.id or name)
    db = SessionLocal()
    try:
        if db.get(Skill, sid):
            raise HTTPException(409, f"skill id 已存在: {sid}")
        row = Skill(
            id=sid,
            name=name,
            description=meta.get('description', ''),
            tags=meta.get('tags', ''),
            department=_norm_department(payload.department, meta),
            author=payload.author or '匿名',
            author_name=payload.author or '',
            content=payload.content,
            updated_by=payload.author or '',
        )
        db.add(row)
        db.commit()
        return {'ok': True, 'id': sid}
    finally:
        db.close()


class SkillUpdate(BaseModel):
    content: str
    author: str = ''
    department: Optional[str] = None


@router.put("/{skill_id}/content")
def update_skill(skill_id: str, payload: SkillUpdate):
    ensure_schema()
    db = SessionLocal()
    try:
        row = db.get(Skill, skill_id)
        if not row:
            raise HTTPException(404, "skill 不存在")
        row.content = payload.content
        parsed = parse_frontmatter(payload.content)
        if parsed['meta'].get('name'):
            row.name = parsed['meta']['name']
        if parsed['meta'].get('description') is not None:
            row.description = parsed['meta']['description']
        if parsed['meta'].get('tags') is not None:
            row.tags = parsed['meta']['tags']
        # 部门: 显式传参 > frontmatter > 保持原值
        new_dept = _norm_department(payload.department, parsed['meta'])
        if payload.department or parsed['meta'].get('department'):
            row.department = new_dept
        if payload.author:
            row.updated_by = payload.author
        db.commit()
        return {'ok': True}
    finally:
        db.close()


@router.delete("/{skill_id}")
def delete_skill(skill_id: str):
    ensure_schema()
    db = SessionLocal()
    try:
        row = db.get(Skill, skill_id)
        if not row:
            raise HTTPException(404, "skill 不存在")
        db.delete(row)
        db.commit()
        return {'ok': True}
    finally:
        db.close()


@router.get("/{skill_id}/download")
def download_skill(skill_id: str):
    ensure_schema()
    db = SessionLocal()
    try:
        row = db.get(Skill, skill_id)
        if not row:
            raise HTTPException(404, "skill 不存在")
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as zf:
            zf.writestr(f'{skill_id}/SKILL.md', row.content or '')
        buf.seek(0)
        row.downloads = (row.downloads or 0) + 1
        db.commit()
        return StreamingResponse(
            buf,
            media_type='application/zip',
            headers={'Content-Disposition': f'attachment; filename="{skill_id}.zip"'},
        )
    finally:
        db.close()


@router.get("/{skill_id}/install-manifest")
def install_manifest(skill_id: str, origin: Optional[str] = None):
    ensure_schema()
    base = (origin or '').rstrip('/') or 'http://localhost:8002'
    db = SessionLocal()
    try:
        row = db.get(Skill, skill_id)
        if not row:
            raise HTTPException(404, "skill 不存在")
        return {
            'id': skill_id,
            'name': row.name,
            'downloadUrl': f'{base}/api/skills/{skill_id}/download',
            'installPath': f'~/.workbuddy/skills/{skill_id}/',
            'steps': [
                f'从 {base}/api/skills/{skill_id}/download 下载 zip',
                f'解压到 ~/.workbuddy/skills/{skill_id}/',
                '确认 SKILL.md 存在并加载该技能',
            ],
            'oneLiner': build_one_liner(skill_id, base),
        }
    finally:
        db.close()


@router.post("/upload-zip")
async def upload_zip(file: UploadFile = File(...), author: str = '匿名', department: Optional[str] = None):
    """上传 zip 包: 自动定位 SKILL.md(支持包内任意层级)"""
    ensure_schema()
    data = await file.read()
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            md_path = next(
                (n for n in zf.namelist()
                 if n.endswith('SKILL.md') and not n.startswith('__MACOSX')),
                None,
            )
            if not md_path:
                raise HTTPException(400, "zip 中未找到 SKILL.md")
            content = zf.read(md_path).decode('utf-8', errors='replace')
    except zipfile.BadZipFile:
        raise HTTPException(400, "无效的 zip 文件")
    return create_skill(SkillCreate(content=content, author=author, department=department))
