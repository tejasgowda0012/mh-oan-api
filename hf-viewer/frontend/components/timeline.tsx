"use client";

import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { Wrench, ArrowDownToLine, Brain, ChevronRight } from "lucide-react";
import { useState } from "react";
import type { TimelineEntry } from "@/lib/types";
import { cn } from "@/lib/utils";
import { JsonBlock } from "./json-block";

// Shared markdown styling so user + assistant render at the exact same size
// (13px) and weight, independent of the typography plugin.
const MD_CLASS =
  "text-[13px] leading-snug break-words " +
  "[&_p]:my-1 [&_p:first-child]:mt-0 [&_p:last-child]:mb-0 " +
  "[&_strong]:font-semibold " +
  "[&_ul]:my-1 [&_ul]:list-disc [&_ul]:pl-4 [&_ol]:my-1 [&_ol]:list-decimal [&_ol]:pl-4 " +
  "[&_li]:my-0.5 [&_a]:underline [&_a]:underline-offset-2 " +
  "[&_code]:rounded [&_code]:bg-black/5 [&_code]:px-1 [&_code]:py-0.5 [&_code]:font-mono [&_code]:text-[12px] dark:[&_code]:bg-white/10";

export function Timeline({ entries }: { entries: TimelineEntry[] }) {
  if (entries.length === 0) {
    return (
      <p className="py-8 text-center text-sm text-muted-foreground">
        No messages could be parsed for this column.
      </p>
    );
  }

  let firstUserSeen = false;
  return (
    <div className="space-y-1.5">
      {entries.map((entry, i) => {
        const isFirstUser = entry.type === "user" && !firstUserSeen;
        if (entry.type === "user") firstUserSeen = true;
        return <TimelineRow key={i} entry={entry} isFirstUser={isFirstUser} />;
      })}
    </div>
  );
}

function TimelineRow({
  entry,
  isFirstUser,
}: {
  entry: TimelineEntry;
  isFirstUser: boolean;
}) {
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
      return <Bubble role="user" content={entry.content} isFirstUser={isFirstUser} />;
    case "assistant":
      return <Bubble role="assistant" content={entry.content} />;
    case "tool-call":
      return (
        <details className="group ml-5 w-fit max-w-full rounded-md border border-amber-300/60 bg-amber-50/70 px-2.5 py-1.5 dark:border-amber-800/50 dark:bg-amber-950/20">
          <summary className="flex cursor-pointer list-none items-center gap-2 text-xs text-amber-700 dark:text-amber-300">
            <ChevronRight className="size-3.5 shrink-0 transition-transform group-open:rotate-90" />
            <Wrench className="size-3.5 shrink-0" />
            <span className="font-mono font-medium">{entry.toolName}</span>
            <span className="text-[10px] uppercase tracking-wide opacity-50">
              call
            </span>
            {entry.toolCallId && (
              <span className="ml-3 font-mono text-[10px] opacity-40">
                {entry.toolCallId}
              </span>
            )}
          </summary>
          <div className="mt-1.5">
            <JsonBlock value={entry.args} />
          </div>
        </details>
      );
    case "tool-return":
      return (
        <details className="group ml-5 w-fit max-w-full rounded-md border border-border/70 bg-muted/20 px-2.5 py-1.5">
          <summary className="flex cursor-pointer list-none items-center gap-2 text-xs text-muted-foreground">
            <ChevronRight className="size-3.5 shrink-0 transition-transform group-open:rotate-90" />
            <ArrowDownToLine className="size-3.5 shrink-0" />
            <span className="font-mono font-medium">{entry.toolName}</span>
            <span className="text-[10px] uppercase tracking-wide opacity-50">
              return
            </span>
            {entry.toolCallId && (
              <span className="ml-3 font-mono text-[10px] opacity-40">
                {entry.toolCallId}
              </span>
            )}
          </summary>
          <div className="mt-1.5">
            <JsonBlock value={entry.content} />
          </div>
        </details>
      );
    default:
      return null;
  }
}

// Context labels that should only be shown on the first user turn.
const CONTEXT_LABEL = /^(selected language|moderation|agristack|information availability)/i;

function parseLabeledFields(text: string): { label: string; value: string }[] {
  const matches = [...text.matchAll(/\*\*([^*\n]+?):\*\*/g)];
  if (matches.length === 0) return [];
  const fields: { label: string; value: string }[] = [];
  for (let i = 0; i < matches.length; i++) {
    const label = matches[i][1].trim();
    const start = (matches[i].index ?? 0) + matches[i][0].length;
    const end =
      i + 1 < matches.length ? matches[i + 1].index ?? text.length : text.length;
    fields.push({ label, value: text.slice(start, end).trim() });
  }
  return fields;
}

/**
 * For the user bubble: put each "**Label:**" field on its own (tight) line.
 * On turns after the first, drop the repeated context fields (moderation /
 * language / agristack) and keep only the actual user question.
 */
function formatUserContent(content: string, isFirst: boolean): string {
  const fields = parseLabeledFields(content);
  if (fields.length === 0) return content;
  const chosen = isFirst
    ? fields
    : fields.filter((f) => !CONTEXT_LABEL.test(f.label));
  const use = chosen.length ? chosen : [fields[0]];
  // Two trailing spaces + newline = markdown hard break (tight line spacing).
  return use.map((f) => `**${f.label}:** ${f.value}`).join("  \n");
}

function Bubble({
  role,
  content,
  isFirstUser = false,
}: {
  role: "user" | "assistant";
  content: string;
  isFirstUser?: boolean;
}) {
  const isUser = role === "user";
  const text = isUser ? formatUserContent(content, isFirstUser) : content;
  return (
    <div className={cn("flex", isUser ? "justify-start" : "justify-end")}>
      <div
        className={cn(
          "max-w-[80%] rounded-lg px-3 py-2",
          isUser
            ? "rounded-bl-sm bg-green-100 text-green-950 dark:bg-green-950/40 dark:text-green-100"
            : "rounded-br-sm bg-blue-100 text-blue-950 dark:bg-blue-950/40 dark:text-blue-100"
        )}
      >
        <div className="mb-1 text-[10px] font-semibold uppercase tracking-wider opacity-50">
          {isUser ? "User" : "Assistant"}
        </div>
        <div className={MD_CLASS}>
          <ReactMarkdown remarkPlugins={[remarkGfm]}>{text}</ReactMarkdown>
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
    <div className="ml-5 w-fit max-w-full rounded-md border border-dashed border-border bg-muted/10">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className="flex w-full items-center gap-2 px-2.5 py-1.5 text-xs font-medium text-muted-foreground"
      >
        <ChevronRight
          className={cn("size-3.5 transition-transform", open && "rotate-90")}
        />
        {icon}
        {label}
      </button>
      {open && (
        <pre className="overflow-x-auto whitespace-pre-wrap break-words px-2.5 pb-2.5 text-[11px] font-mono text-muted-foreground">
          {content}
        </pre>
      )}
    </div>
  );
}
