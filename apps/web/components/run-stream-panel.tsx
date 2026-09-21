"use client";

import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { statusLabel } from "@/lib/labels";

const API_BASE_URL = (process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000").replace(/\/$/, "");

type StreamPayload = {
  id?: string;
  run_id?: string;
  project_id?: string;
  sequence?: number;
  job_id?: string;
  node_name?: string;
  turn_index?: number;
  status?: string;
  run_status?: string;
  project_status?: string;
  turn_count?: number;
  orchestrator?: string;
  message?: string;
};

type StreamEvent = {
  id: string;
  event: string;
  data: StreamPayload;
};

type ParsedSseFrame = {
  id?: string;
  event: string;
  data: StreamPayload;
};

const EVENT_LABELS: Record<string, string> = {
  run_queued: "运行已排队",
  run_retry_queued: "重试已排队",
  run_started: "Worker 已启动",
  node_completed: "节点完成",
  run_completed: "运行完成",
  run_failed: "运行失败",
  run_cancelled: "运行已取消",
};

function valueAfterColon(line: string): string | null {
  const index = line.indexOf(":");
  if (index === -1) {
    return null;
  }
  const raw = line.slice(index + 1);
  return raw.startsWith(" ") ? raw.slice(1) : raw;
}

const NODE_LABELS: Record<string, string> = {
  load_state: "加载状态",
  build_context: "构建上下文",
  analyze_sources: "分析输入事实",
  update_analysis: "保存分析草稿",
  verify_package: "验证分析质量",
  decide_next_state: "决定下一状态",
  persist_state: "保存最终状态",
};

function parseSseFrame(frame: string): ParsedSseFrame | null {
  let id: string | undefined;
  let event = "";
  const dataLines: string[] = [];

  for (const line of frame.split("\n")) {
    if (line.startsWith(":")) {
      continue;
    }
    if (line.startsWith("id:")) {
      id = valueAfterColon(line) ?? undefined;
    }
    if (line.startsWith("event:")) {
      event = valueAfterColon(line) ?? "";
    }
    if (line.startsWith("data:")) {
      const value = valueAfterColon(line);
      if (value !== null) {
        dataLines.push(value);
      }
    }
  }

  if (!event || dataLines.length === 0) {
    return null;
  }

  return {
    id,
    event,
    data: JSON.parse(dataLines.join("\n")) as StreamPayload,
  };
}

function eventTitle(event: StreamEvent): string {
  if (event.event === "node_completed" && event.data.node_name) {
    return NODE_LABELS[event.data.node_name] ?? event.data.node_name;
  }
  return EVENT_LABELS[event.event] ?? event.event;
}

function eventMeta(event: StreamEvent): string {
  if (event.event === "node_completed") {
    return `#${event.data.turn_index ?? "-"} · ${event.data.node_name ?? "-"} · ${event.data.orchestrator ?? "-"}`;
  }
  if (event.event === "run_completed") {
    return `${statusLabel(event.data.status)} · ${event.data.turn_count ?? 0} 个节点`;
  }
  if (event.event === "run_failed") {
    return event.data.message ?? "未知异常";
  }
  if (event.data.status || event.data.job_id) {
    return `${statusLabel(event.data.status)} · job ${event.data.job_id ?? "-"}`;
  }
  return event.data.run_id ?? "-";
}

export function RunStreamPanel({ projectId, runId }: { projectId: string; runId: string | null }) {
  const router = useRouter();
  const [events, setEvents] = useState<StreamEvent[]>([]);
  const [isRunning, setIsRunning] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [completed, setCompleted] = useState(false);
  const [lastSequence, setLastSequence] = useState(0);

  useEffect(() => {
    setEvents([]);
    setError(null);
    setCompleted(false);
    setLastSequence(0);
  }, [runId]);

  async function startStream() {
    if (!runId) {
      return;
    }
    const reconnectAfterSequence = lastSequence;
    if (reconnectAfterSequence === 0) {
      setEvents([]);
      setCompleted(false);
    }
    setError(null);
    setIsRunning(true);

    const pushFrame = (rawFrame: string) => {
      const parsed = parseSseFrame(rawFrame);
      if (!parsed) {
        return;
      }
      const sequence = parsed.data.sequence;
      if (typeof sequence === "number") {
        setLastSequence((current) => Math.max(current, sequence));
      }
      const nextEvent: StreamEvent = {
        id: parsed.data.id ?? (typeof sequence === "number" ? `${parsed.event}-${sequence}` : (parsed.id ?? parsed.event)),
        event: parsed.event,
        data: parsed.data,
      };
      setEvents((current) => [...current, nextEvent]);
      if (["run_completed", "run_failed", "run_cancelled"].includes(parsed.event)) {
        setCompleted(true);
      }
      if (parsed.event === "run_failed") {
        setError(parsed.data.message ?? "运行失败");
      }
    };

    try {
      const streamUrl = new URL(`${API_BASE_URL}/projects/${projectId}/runs/${runId}/events/stream`);
      streamUrl.searchParams.set("after_sequence", String(reconnectAfterSequence));
      const response = await fetch(streamUrl.toString(), {
        method: "GET",
        headers: {
          Accept: "text/event-stream",
        },
      });

      if (!response.ok) {
        const body = await response.text();
        throw new Error(body || `请求失败：${response.status}`);
      }
      if (!response.body) {
        throw new Error("浏览器未返回可读事件流");
      }

      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";

      while (true) {
        const { value, done } = await reader.read();
        if (done) {
          break;
        }
        buffer += decoder.decode(value, { stream: true });
        const frames = buffer.split("\n\n");
        buffer = frames.pop() ?? "";
        frames.forEach(pushFrame);
      }

      buffer += decoder.decode();
      if (buffer.trim()) {
        pushFrame(buffer);
      }
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "流式运行失败");
    } finally {
      setIsRunning(false);
    }
  }

  return (
    <section className="panel stack">
      <div className="toolbar">
        <div>
          <h2>调研事件</h2>
          <p className="muted">
            {runId ? (isRunning ? "正在实时订阅调研事件" : "实时订阅调研事件") : "启动调研 Loop 后会生成调研事件"}
          </p>
        </div>
        <div className="toolbar-actions">
          {completed ? (
            <button className="button button-secondary" type="button" onClick={() => router.refresh()}>
              刷新结果
            </button>
          ) : null}
          <button className="button" type="button" onClick={startStream} disabled={!runId || isRunning}>
            {isRunning ? "订阅中" : "订阅事件"}
          </button>
        </div>
      </div>

      {error ? <p className="error-text">{error}</p> : null}

      {events.length ? (
        <div className="stream-events">
          {events.map((event) => (
            <div className="stream-event" key={event.id}>
              <span className="stream-event__type">{EVENT_LABELS[event.event] ?? event.event}</span>
              <strong>{eventTitle(event)}</strong>
              <span className="meta">{eventMeta(event)}</span>
            </div>
          ))}
        </div>
      ) : (
        <p className="muted">{runId ? "等待读取当前调研 run 的持久事件。" : "还没有可订阅的调研 run。"}</p>
      )}
    </section>
  );
}
