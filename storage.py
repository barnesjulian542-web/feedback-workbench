"""业务数据库；与 LangGraph 检查点数据库分开。"""
import hashlib
import json
import re
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from models import Analysis, ClarificationInput, validate_review_action


def now():
    return datetime.now(timezone.utc).isoformat()


class Store:
    def __init__(self, data_dir):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.path = self.data_dir / "business.sqlite"
        with self.connect() as con:
            con.executescript("""
                CREATE TABLE IF NOT EXISTS feedback (
                    id TEXT PRIMARY KEY, source_id TEXT NOT NULL, text TEXT NOT NULL,
                    llm_mode TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending',
                    outcome TEXT, created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS requirements (
                    id TEXT PRIMARY KEY, title TEXT NOT NULL, problem TEXT NOT NULL,
                    category TEXT NOT NULL, priority TEXT NOT NULL,
                    analysis_json TEXT NOT NULL, created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS feedback_links (
                    feedback_id TEXT PRIMARY KEY REFERENCES feedback(id),
                    requirement_id TEXT NOT NULL REFERENCES requirements(id)
                );
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, feedback_id TEXT NOT NULL,
                    node TEXT NOT NULL, detail TEXT NOT NULL, created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS clarifications (
                    feedback_id TEXT NOT NULL REFERENCES feedback(id),
                    round_no INTEGER NOT NULL, text TEXT NOT NULL, created_at TEXT NOT NULL,
                    PRIMARY KEY(feedback_id, round_no)
                );
            """)
            # 为已有业务数据库补齐字段，原始反馈和已有审核结果继续保留。
            columns = {row["name"] for row in con.execute("PRAGMA table_info(feedback)")}
            for name in ("context_text", "clarification_note", "latest_analysis_json"):
                if name not in columns:
                    con.execute(f"ALTER TABLE feedback ADD COLUMN {name} TEXT")

    @contextmanager
    def connect(self):
        con = sqlite3.connect(self.path, timeout=20)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA foreign_keys=ON")
        try:
            with con:
                yield con
        finally:
            con.close()

    def register(self, text, source_id="", llm_mode="mock"):
        text = text.strip()
        if not text or len(text) > 5000:
            raise ValueError("请输入 1—5000 字的反馈")
        # 有来源 ID 时按该事件去重；手动输入无 ID 时按原文去重。
        source_id = source_id.strip()
        key = "id:" + source_id if source_id else "text:" + text
        feedback_id = hashlib.sha256(key.encode("utf-8")).hexdigest()
        with self.connect() as con:
            con.execute(
                "INSERT OR IGNORE INTO feedback(id,source_id,text,llm_mode,created_at) VALUES(?,?,?,?,?)",
                (feedback_id, source_id, text, llm_mode, now()),
            )
            row = dict(con.execute("SELECT * FROM feedback WHERE id=?", (feedback_id,)).fetchone())
        if row["text"] != text:
            raise ValueError("相同来源 ID 已存在，但原文不同；请使用新的来源 ID")
        return row

    def get_feedback(self, feedback_id):
        with self.connect() as con:
            row = con.execute("SELECT * FROM feedback WHERE id=?", (feedback_id,)).fetchone()
            if row is None:
                raise ValueError("找不到这条反馈")
            return dict(row)

    def list_feedback(self):
        with self.connect() as con:
            return [dict(r) for r in con.execute("SELECT * FROM feedback ORDER BY created_at DESC")]

    def list_requirements(self):
        with self.connect() as con:
            return [dict(r) for r in con.execute("""
                SELECT r.*, COUNT(l.feedback_id) AS feedback_count FROM requirements r
                LEFT JOIN feedback_links l ON r.id=l.requirement_id
                GROUP BY r.id ORDER BY r.created_at DESC
            """)]

    @staticmethod
    def _grams(text):
        text = re.sub(r"\W+", "", text.lower())
        return {text[i:i + 2] for i in range(max(len(text) - 1, 0))}

    def search_requirements(self, query, limit=5):
        """教学基线：字符二元组重合度；不是 Embedding，也不是概率。"""
        q = self._grams(query)
        results = []
        for row in self.list_requirements():
            grams = self._grams(row["title"] + row["problem"])
            score = len(q & grams) / len(q | grams) if q | grams else 0.0
            if score > 0:
                results.append({k: row[k] for k in ("id", "title", "problem", "category", "feedback_count")} | {"similarity": round(score, 4)})
        return sorted(results, key=lambda item: item["similarity"], reverse=True)[:limit]

    def has_requirement(self, requirement_id):
        with self.connect() as con:
            return con.execute("SELECT 1 FROM requirements WHERE id=?", (requirement_id,)).fetchone() is not None

    def log(self, feedback_id, node, detail):
        with self.connect() as con:
            con.execute("INSERT INTO events(feedback_id,node,detail,created_at) VALUES(?,?,?,?)",
                        (feedback_id, node, json.dumps(detail, ensure_ascii=False), now()))

    def list_events(self, feedback_id):
        with self.connect() as con:
            return [dict(r) for r in con.execute("SELECT * FROM events WHERE feedback_id=? ORDER BY id", (feedback_id,))]

    def save_analysis(self, feedback_id, analysis):
        with self.connect() as con:
            con.execute("UPDATE feedback SET latest_analysis_json=? WHERE id=?",
                        (analysis.model_dump_json(), feedback_id))

    def list_clarifications(self, feedback_id):
        with self.connect() as con:
            return [dict(r) for r in con.execute(
                "SELECT * FROM clarifications WHERE feedback_id=? ORDER BY round_no", (feedback_id,))]

    def wait_for_clarification(self, feedback_id, round_no, note):
        """中断节点重跑时重复调用安全；已收到本轮补充时不倒退状态。"""
        with self.connect() as con:
            con.execute("BEGIN IMMEDIATE")
            if con.execute("SELECT 1 FROM clarifications WHERE feedback_id=? AND round_no=?",
                           (feedback_id, round_no)).fetchone():
                return
            row = con.execute("SELECT status,clarification_note FROM feedback WHERE id=?", (feedback_id,)).fetchone()
            if row is None or row["status"] == "done":
                raise ValueError("这条反馈无法进入待补充状态")
            if row["status"] == "waiting_clarification" and row["clarification_note"] == note:
                return
            con.execute("UPDATE feedback SET status='waiting_clarification',clarification_note=? WHERE id=?",
                        (note, feedback_id))
            detail = {"stage": "waiting", "round_no": round_no, "note": note}
            con.execute("INSERT INTO events(feedback_id,node,detail,created_at) VALUES(?,?,?,?)",
                        (feedback_id, "clarify", json.dumps(detail, ensure_ascii=False), now()))

    def preview_clarification(self, feedback_id, round_no, supplement):
        supplement = ClarificationInput(supplement=supplement).supplement
        record = self.get_feedback(feedback_id)
        parts = self.list_clarifications(feedback_id)
        existing = next((p for p in parts if p["round_no"] == round_no), None)
        if existing and existing["text"] != supplement:
            raise ValueError("本轮补充已保存，请先重试未完成的分析")
        if not existing:
            parts.append({"round_no": round_no, "text": supplement})
        context = "\n\n".join([record["text"]] + [p["text"] for p in sorted(parts, key=lambda p: p["round_no"])])
        if len(context) > 10000:
            raise ValueError("原文与补充说明合计不能超过 10000 字")
        return context

    def add_clarification(self, feedback_id, round_no, supplement):
        supplement = ClarificationInput(supplement=supplement).supplement
        context = self.preview_clarification(feedback_id, round_no, supplement)
        with self.connect() as con:
            con.execute("BEGIN IMMEDIATE")
            row = con.execute("SELECT status FROM feedback WHERE id=?", (feedback_id,)).fetchone()
            if row is None or row["status"] == "done":
                raise ValueError("这条反馈无法继续补充")
            prior = con.execute("SELECT text FROM clarifications WHERE feedback_id=? AND round_no=?",
                                (feedback_id, round_no)).fetchone()
            if prior:
                if prior["text"] != supplement:
                    raise ValueError("本轮补充已保存，请先重试未完成的分析")
                return context
            con.execute("INSERT INTO clarifications VALUES(?,?,?,?)", (feedback_id, round_no, supplement, now()))
            con.execute("UPDATE feedback SET status='pending',context_text=?,latest_analysis_json=NULL WHERE id=?",
                        (context, feedback_id))
            detail = {"stage": "received", "round_no": round_no, "supplement": supplement}
            con.execute("INSERT INTO events(feedback_id,node,detail,created_at) VALUES(?,?,?,?)",
                        (feedback_id, "clarify", json.dumps(detail, ensure_ascii=False), now()))
        return context

    def persist(self, feedback_id, decision, analysis):
        """事务＋反馈唯一关联，重试同一写入不会重复创建需求。"""
        with self.connect() as con:
            con.execute("BEGIN IMMEDIATE")
            row = con.execute("SELECT status,outcome,latest_analysis_json FROM feedback WHERE id=?", (feedback_id,)).fetchone()
            if row["status"] == "done":
                return json.loads(row["outcome"])
            if decision.action == "clarify":
                raise ValueError("待补充信息应进入等待节点，不能作为已完成反馈保存")
            original = Analysis.model_validate_json(row["latest_analysis_json"]) if row["latest_analysis_json"] else None
            validate_review_action(decision.action, analysis, original)
            requirement_id = None
            if decision.action == "create":
                requirement_id = str(uuid.uuid4())
                con.execute("INSERT INTO requirements VALUES(?,?,?,?,?,?,?)", (
                    requirement_id, analysis.title, analysis.problem, analysis.category,
                    decision.priority, analysis.model_dump_json(), now(),
                ))
            elif decision.action == "link":
                requirement_id = decision.requirement_id
                if not con.execute("SELECT 1 FROM requirements WHERE id=?", (requirement_id,)).fetchone():
                    raise ValueError("所选需求不存在")
            if requirement_id:
                con.execute("INSERT INTO feedback_links VALUES(?,?)", (feedback_id, requirement_id))
            outcome = {"action": decision.action, "requirement_id": requirement_id,
                       "note": decision.note, "completed_at": now()}
            con.execute("UPDATE feedback SET status='done',outcome=? WHERE id=?",
                        (json.dumps(outcome, ensure_ascii=False), feedback_id))
            return outcome
