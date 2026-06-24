"use client";

import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import {
  Wrench,
  ArrowDownToLine,
  Brain,
  ChevronRight,
} from "lucide-react";
import { useState } from "react";
import type { TimelineEntry } from "@/lib/types";
import { cn } from "@/lib/utils";
import { JsonBlock } from "./json-block";

export function Timeline({ entries }: { entries: TimelineEntry[] }) {
  if (entries.length === 0) {
    return (
      <p className="py-8 text-center text-sm text-muted-foreground">
        No messages could be parsed for this column.
      </p>
    );
  }

  return (
    <div className="space-y-2">
      {entries.map((entry, i) => (
        <TimelineRow key={i} entry={entry} />
      ))}
    </div>
  );
}

function TimelineRow({ entry }: { entry: TimelineEntry }) {
  switch (entry.type) {
    case "system":
      return <CollapsibleBlock label="System prompt" content={entry.content} />;
    case "thinking":
      return (
        <CollapsibleBlock
          label="Thinking"
          content={entry.content}
          icon={<Brain className="size-3.5" />}
        />
      );
    case "user":
      return <Bubble role="user" content={entry.content} />;
    case "assistant":
      return <Bubble role="assistant" content={entry.content} />;
    case "tool-call":
      return (
        <div className="ml-6 rounded-md border border-amber-300/60 bg-amber-50 px-3 py-2 dark:border-amber-800/60 dark:bg-amber-950/30">
          <div className="mb-1.5 flex items-center gap-1.5 text-xs font-medium text-amber-800 dark:text-amber-300">
            <Wrench className="size-3.5" />
            Tool call: <span className="font-mono">{entry.toolName}</span>
            {entry.toolCallId && (
              <span className="ml-auto font-mono text-[10px] opacity-50">
                {entry.toolCallId}
              </span>
            )}
          </div>
          <JsonBlock value={entry.args} />
        </div>
      );
    case "tool-return":
      return (
        <details className="ml-6 rounded-md border border-border bg-muted/30 px-3 py-2">
          <summary className="flex cursor-pointer list-none items-center gap-1.5 text-xs font-medium text-muted-foreground">
            <ArrowDownToLine className="size-3.5" />
            Tool return: <span className="font-mono">{entry.toolName}</span>
            {entry.toolCallId && (
              <span className="ml-auto font-mono text-[10px] opacity-50">
                {entry.toolCallId}
              </span>
            )}
          </summary>
          <div className="mt-2">
            <JsonBlock value={entry.content} />
          </div>
        </details>
      );
    default:
      return null;
  }
}

function Bubble({
  role,
  content,
}: {
  role: "user" | "assistant";
  content: string;
}) {
  const isUser = role === "user";
  return (
    <div className={cn("flex", isUser ? "justify-start" : "justify-end")}>
      <div
        className={cn(
          "max-w-[85%] rounded-xl px-4 py-3 text-sm",
          isUser
            ? "rounded-bl-sm bg-green-100 text-green-950 dark:bg-green-950/40 dark:text-green-100"
            : "rounded-br-sm bg-blue-100 text-blue-950 dark:bg-blue-950/40 dark:text-blue-100"
        )}
      >
        <div className="mb-1 text-xs font-medium opacity-60">
          {isUser ? "User" : "Assistant"}
        </div>
        <div className="prose prose-sm dark:prose-invert max-w-none [&_p]:mb-1 [&_p:last-child]:mb-0">
          <ReactMarkdown remarkPlugins={[remarkGfm]}>{content}</ReactMarkdown>
        </div>
      </div>
    </div>
  );
}

function CollapsibleBlock({
  label,
  content,
  icon,
}: {
  label: string;
  content: string;
  icon?: React.ReactNode;
}) {
  const [open, setOpen] = useState(false);
  return (
    <div className="rounded-md border border-dashed border-border bg-muted/20">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className="flex w-full items-center gap-1.5 px-3 py-2 text-xs font-medium text-muted-foreground"
      >
        <ChevronRight
          className={cn("size-3.5 transition-transform", open && "rotate-90")}
        />
        {icon}
        {label}
      </button>
      {open && (
        <pre className="overflow-x-auto px-3 pb-3 text-xs font-mono whitespace-pre-wrap break-words text-muted-foreground">
          {content}
        </pre>
      )}
    </div>
  );
}
