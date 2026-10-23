"use client";

import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";
import styles from "./page.module.css";

type Evidence = { id: string; path: string; start_line: number; end_line: number; content: string; file_hash: string };
type Message = { id: string; role: string; mode: string; status: string; content: string; run_id: string | null;
  evidence_package_id: string | null; citations_json: Evidence[]; report_id: string | null };
type Conversation = { id: string; title: string; project_id: string | null; messages: Message[] };
type Summary = Pick<Conversation, "id" | "title" | "project_id">;
const API = (process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000").replace(/\/$/, "");
const busyStates = new Set(["queued", "running"]);

async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API}/assistant${path}`, { ...init,
    headers: { "Content-Type": "application/json" }, cache: "no-store" });
  if (!response.ok) {
    const error = await response.json().catch(() => ({}));
    throw new Error(typeof error.detail === "string" ? error.detail : "请求失败，请检查服务是否启动。");
  }
  return response.json();
}

export default function AssistantPage() {
  const [list, setList] = useState<Summary[]>([]);
  const [conversation, setConversation] = useState<Conversation | null>(null);
  const [text, setText] = useState("");
  const [source, setSource] = useState("");
  const [mode, setMode] = useState("auto");
  const [sending, setSending] = useState(false);
  const [error, setError] = useState("");
  const [progress, setProgress] = useState("");
  const [evidence, setEvidence] = useState<Evidence[] | null>(null);
  const bottom = useRef<HTMLDivElement>(null);
  const activeId = useRef<string | null>(null);
  const active = conversation?.messages.find(m => busyStates.has(m.status));
  const busy = sending || !!active;

  const refreshList = useCallback(async () => setList(await api<Summary[]>("/conversations")), []);
  const load = useCallback(async (id: string) => {
    activeId.current = id;
    const value = await api<Conversation>(`/conversations/${id}`);
    if (activeId.current !== id) return;
    setConversation(value);
    localStorage.setItem("research-conversation", id);
  }, []);

  useEffect(() => {
    refreshList().catch(e => setError(e.message));
    const id = localStorage.getItem("research-conversation");
    if (id) load(id).catch(e => setError(e.message));
  }, [load, refreshList]);

  useEffect(() => {
    if (!active?.run_id || !conversation?.project_id) return;
    const id = conversation.id;
    const key = `research-cursor:${active.run_id}`;
    let timer: ReturnType<typeof setInterval>;
    const refresh = async () => {
      const value = await api<Conversation>(`/conversations/${id}`);
      if (activeId.current === id) setConversation(value);
    };
    const stream = new EventSource(`${API}/projects/${conversation.project_id}/runs/${active.run_id}/events/stream?after_sequence=${localStorage.getItem(key) ?? "0"}`);
    const update = (event: MessageEvent) => {
      const data = JSON.parse(event.data);
      localStorage.setItem(key, String(data.sequence));
      const labels: Record<string, string> = { repository_prepared: "项目已接入", inventory_ready: "已整理项目目录",
        evidence_checked: "正在核对源码", research_plan: "正在分析问题", research_tool: "正在查找证据",
        evidence_package_ready: "已整理证据包", assistant_completed: "调研完成" };
      setProgress(labels[data.node_name] ?? "正在分析与生成结果…");
      void refresh().catch(e => setError(e.message));
    };
    stream.addEventListener("node_completed", update as EventListener);
    const done = () => { stream.close(); void refresh().catch(e => setError(e.message)); void refreshList().catch(e => setError(e.message)); };
    for (const name of ["run_completed", "run_failed", "run_cancelled"]) stream.addEventListener(name, done);
    // Status polling also covers a stopped worker, cancellations and reconnects.
    timer = setInterval(() => { void refresh().catch(e => setError(e.message)); }, 2500);
    return () => { stream.close(); clearInterval(timer); };
  }, [active?.run_id, conversation?.id, conversation?.project_id, load, refreshList]);

  useEffect(() => { bottom.current?.scrollIntoView({ behavior: "smooth" }); }, [conversation?.messages.length, progress]);

  async function startNew() {
    setError(""); setEvidence(null); setSource(""); setProgress("");
    try {
      const value = await api<Conversation>("/conversations", { method: "POST" });
      await load(value.id); await refreshList();
    } catch (e) { setError((e as Error).message); }
  }

  async function send() {
    if (busy || (!text.trim() && !source.trim())) return;
    setSending(true); setError(""); setEvidence(null); setProgress("准备开始…");
    try {
      let id = conversation?.id;
      if (!id) id = (await api<Conversation>("/conversations", { method: "POST" })).id;
      await api<Message>(`/conversations/${id}/messages`, { method: "POST", body: JSON.stringify({
        content: text.trim() || (mode === "report" ? "请生成这个项目的调研报告" : "请介绍这个项目的入口和主要结构"),
        mode, source: source.trim() || null }) });
      setText(""); setSource(""); await load(id); await refreshList();
    } catch (e) { setError((e as Error).message); }
    finally { setSending(false); }
  }

  async function showEvidence(packageId: string) {
    if (!conversation) return;
    try {
      const pack = await api<{ items: Evidence[] }>(`/conversations/${conversation.id}/evidence/${packageId}`);
      setEvidence(pack.items);
    } catch (e) { setError((e as Error).message); }
  }

  async function cancel() {
    if (!active?.run_id || !conversation?.project_id) return;
    try {
      const response = await fetch(`${API}/projects/${conversation.project_id}/runs/${active.run_id}/cancel`, { method: "POST" });
      if (!response.ok) throw new Error("暂时无法停止，请稍后重试。");
      await load(conversation.id);
    } catch (e) { setError((e as Error).message); }
  }

  return <div className={styles.workspace}>
    <aside className={styles.sidebar}>
      <button className={styles.primary} onClick={startNew} disabled={sending}>＋ 新建会话</button>
      <p className={styles.caption}>最近的调研</p>
      {list.map(c => <button key={c.id} className={conversation?.id === c.id ? styles.selected : styles.conversation}
        onClick={() => { setEvidence(null); setError(""); setProgress(""); void load(c.id).catch(e => setError(e.message)); }}>{c.title}</button>)}
      <Link href="/projects" className={styles.workbench}>查看项目工作台 ↗</Link>
      <Link href="/settings/llm" className={styles.workbench}>模型设置</Link>
    </aside>
    <section className={styles.chat}>
      <header className={styles.header}><div><h1>个人调研助手</h1><p>从一个问题开始，让结论有据可查。</p></div>
        {conversation?.project_id && <Link href={`/projects/${conversation.project_id}`}>查看当前项目 ↗</Link>}
      </header>
      <div className={styles.messages}>
        {!conversation?.messages.length && <div className={styles.welcome}>
          <span className={styles.eyebrow}>探索 · 查证 · 整理</span><h2>今天想了解哪个项目？</h2>
          <p>可以先聊聊，也可以提供 GitHub 地址或本机项目路径。选择项目问答或生成报告，查看结论与对应源码。</p>
          <div className={styles.examples}>
            {["接手一个陌生代码库应该先看什么？", "这个项目的入口在哪里？", "请生成架构与风险调研报告"].map((s, i) =>
              <button key={s} onClick={() => { setText(s); setMode(i === 0 ? "chat" : i === 1 ? "qa" : "report"); }}>{s}</button>)}
          </div></div>}
        {conversation?.messages.map(m => <article key={m.id} className={m.role === "user" ? styles.user : styles.assistant}>
          <div className={styles.messageLabel}>{m.role === "user" ? "你" : "调研助手"}
            {m.role === "assistant" && <span>{m.mode === "report" ? "调研报告" : m.mode === "qa" ? "项目问答" : "聊天"}</span>}</div>
          <div className={styles.content}>{m.content || (busyStates.has(m.status) ? progress || "等待调研任务执行…" : "暂无内容")}</div>
          {m.evidence_package_id && <button className={styles.evidenceButton} onClick={() => void showEvidence(m.evidence_package_id!)}>查看本次证据包 ↗</button>}
          {m.report_id && conversation.project_id && <Link className={styles.evidenceButton} href={`/projects/${conversation.project_id}/report`}>查看报告与导出 ↗</Link>}
          {m.status === "awaiting_input" && conversation.project_id && <Link href={`/projects/${conversation.project_id}/run`}>补充调研信息 ↗</Link>}
        </article>)}
        <div ref={bottom} />
      </div>
      {error && <div role="alert" className={styles.error}>{error}</div>}
      <form className={styles.composer} onSubmit={e => { e.preventDefault(); void send(); }}>
        <div className={styles.controls}><select aria-label="对话方式" value={mode} disabled={busy} onChange={e => setMode(e.target.value)}>
          <option value="auto">自动判断</option><option value="chat">普通聊天</option><option value="qa">项目问答</option><option value="report">生成报告</option>
        </select><input aria-label="项目地址" disabled={busy} value={source} onChange={e => setSource(e.target.value)} placeholder="GitHub 地址或本机路径（接入一次即可）" /></div>
        <textarea aria-label="输入问题" disabled={busy} value={text} onChange={e => setText(e.target.value)} placeholder="输入问题，或描述希望生成的报告…" rows={3} />
        <div className={styles.sendRow}><small>{active ? "正在调研；若长时间等待，请检查后台 Worker 是否已启动。" : "项目回答与报告均可查看对应证据。"}</small>
          {active ? <button type="button" onClick={() => void cancel()}>停止</button> : <button className={styles.primary} type="submit" disabled={sending || (!text.trim() && !source.trim())}>{sending ? "发送中…" : "发送 ↑"}</button>}</div>
      </form>
    </section>
    {evidence !== null && <aside className={styles.evidence}><div className={styles.evidenceHeader}><h2>源码证据</h2><button onClick={() => setEvidence(null)}>关闭</button></div>
      {!evidence.length && <p>本次尚未找到可引用的源码片段。</p>}
      {evidence.map(item => <details key={item.id} open><summary>{item.path}:{item.start_line}–{item.end_line}</summary>
        <small>{item.id} · SHA-256 {item.file_hash.slice(0, 12)}</small><pre>{item.content}</pre></details>)}
      <p className={styles.caption}>这里保存的是本次调研时读取的源码，便于核对结论与文件版本。</p>
    </aside>}
  </div>;
}
