import {
  ActionIcon,
  Alert,
  Anchor,
  Badge,
  Box,
  Button,
  Card,
  Divider,
  Group,
  Loader,
  Paper,
  Select,
  Stack,
  Tabs,
  Text,
  Textarea,
  ThemeIcon,
  Title,
  useComputedColorScheme,
} from "@mantine/core";
import {
  IconArrowBackUp,
  IconBrandGoogleFilled,
  IconChevronUp,
  IconDots,
  IconMessages,
  IconPaperclip,
  IconSparkles,
  IconThumbDown,
  IconThumbUp,
} from "@tabler/icons-react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { type ReactNode, useEffect, useMemo, useRef, useState } from "react";
import { useParams } from "react-router";

import {
  api,
  ApiError,
  type Suggestion,
  type TicketDetail,
  type TicketMessage,
  useCanned,
  useMe,
  useStores,
  useSuggestion,
  useTicket,
  useUsers,
} from "../api";

const STATUSES = ["new", "open", "waiting_customer", "resolved", "closed"];
const PRIORITIES = ["low", "normal", "high", "urgent"];

export default function TicketView() {
  const { id } = useParams();
  const ticketId = Number(id);
  const ticket = useTicket(ticketId);

  if (ticket.isPending) return <Loader />;
  if (ticket.isError || !ticket.data) return <Alert color="red">Could not load this ticket.</Alert>;
  return <Loaded t={ticket.data} />;
}

function Loaded({ t }: { t: TicketDetail }) {
  const me = useMe();
  const queryClient = useQueryClient();
  const draftKey = `draft:${t.id}`;
  const [reply, setReply] = useState(() => localStorage.getItem(draftKey) ?? "");
  useEffect(() => localStorage.setItem(draftKey, reply), [reply, draftKey]);
  const refresh = () => queryClient.invalidateQueries({ queryKey: ["ticket", t.id] });
  const patch = useMutation({
    mutationFn: (body: object) => api(`/tickets/${t.id}`, "PATCH", body),
    onSuccess: () => {
      refresh();
      queryClient.invalidateQueries({ queryKey: ["tickets"] });
    },
  });

  return (
    <Stack>
      <Group justify="space-between" align="flex-start" wrap="nowrap">
        <div style={{ minWidth: 0 }}>
          <Title order={3} style={{ overflowWrap: "anywhere" }}>
            #{t.number} · {t.subject || "(no subject)"}
          </Title>
          <Group gap="xs" mt={4}>
            <Badge variant="light">{t.store_name}</Badge>
            <Badge color="gray">{t.status.replace("_", " ")}</Badge>
            {t.delivery_failed && <Badge color="red">delivery failed</Badge>}
          </Group>
        </div>
        {t.gmail_thread_id && (
          <Button
            component="a"
            href={`https://mail.google.com/mail/u/0/#all/${t.gmail_thread_id}`}
            target="_blank"
            rel="noreferrer"
            variant="default"
            size="xs"
            leftSection={<IconBrandGoogleFilled size={15} />}
            style={{ flexShrink: 0 }}
          >
            Open in Gmail
          </Button>
        )}
      </Group>

      <MetaBar ticket={t} meRole={me.data?.role} meId={me.data?.id} patch={patch.mutate} />
      <Thread messages={t.messages} />
      <SuggestionPanel ticketId={t.id} onUse={(text) => setReply(text)} />
      <Composer ticket={t} reply={reply} setReply={setReply} draftKey={draftKey} onDone={refresh} />
      <BottomInfo ticket={t} />
    </Stack>
  );
}

const BUBBLE_BG: Record<string, string> = {
  note: "light-dark(var(--mantine-color-yellow-0), color-mix(in srgb, var(--mantine-color-yellow-6) 16%, transparent))",
  outbound: "light-dark(var(--mantine-color-brand-0), color-mix(in srgb, var(--mantine-color-brand-6) 15%, transparent))",
  inbound: "light-dark(var(--mantine-color-gray-0), var(--mantine-color-dark-6))",
};

const WRAP = { whiteSpace: "pre-wrap", overflowWrap: "anywhere", wordBreak: "break-word" } as const;

// Find where an email's quoted reply history starts, so we can fold it away like Gmail.
function quoteBoundary(lines: string[]): number {
  const wrote = (s?: string) => !!s && /\bwrote:\s*$/.test(s.trim());
  for (let i = 0; i < lines.length; i++) {
    const l = lines[i].trim();
    if (l.startsWith(">")) return i;
    if (/^-{2,}\s*original message\s*-{2,}/i.test(l)) return i;
    if (/^_{5,}$/.test(l)) return i;
    // "On <date>, <name> wrote:" — the attribution can wrap over 1–2 lines.
    if (l.startsWith("On ") && (wrote(l) || wrote(lines[i + 1]) || wrote(lines[i + 2]))) return i;
  }
  return -1;
}

function splitQuoted(text: string): { visible: string; quoted: string } {
  const lines = text.split("\n");
  const at = quoteBoundary(lines);
  if (at <= 0) return { visible: text, quoted: "" }; // nothing quoted, or the whole message is a quote
  const visible = lines.slice(0, at).join("\n").trimEnd();
  if (!visible.trim()) return { visible: text, quoted: "" };
  return { visible, quoted: lines.slice(at).join("\n").trimEnd() };
}

function MessageBody({ text }: { text: string }) {
  const [open, setOpen] = useState(false);
  const { visible, quoted } = splitQuoted(text);
  return (
    <>
      <Text size="sm" style={WRAP}>
        {visible}
      </Text>
      {quoted && (
        <>
          <Button
            variant="subtle"
            color="gray"
            size="compact-xs"
            mt={6}
            leftSection={open ? <IconChevronUp size={13} /> : <IconDots size={13} />}
            onClick={() => setOpen((o) => !o)}
          >
            {open ? "Hide quoted text" : "Show quoted text"}
          </Button>
          {open && (
            <Text
              size="xs"
              c="dimmed"
              mt={6}
              style={{ ...WRAP, borderLeft: "2px solid var(--mantine-color-default-border)", paddingLeft: 10 }}
            >
              {quoted}
            </Text>
          )}
        </>
      )}
    </>
  );
}

// In dark mode we flip the whole email (which is authored for a light background)
// and re-flip media, so the content area is dark without recolouring every email by hand.
const DARK_FILTER = "invert(0.905) hue-rotate(180deg)";

function frameStyle(dark: boolean): string {
  return `
  html{background:${dark ? "#1b1c1e" : "#ffffff"};}
  body{margin:0;padding:6px 8px;background:#ffffff;color:#1b1b1b;
       font:14px/1.55 Inter,-apple-system,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;
       word-break:break-word;overflow-wrap:anywhere;overflow-x:auto;
       ${dark ? `filter:${DARK_FILTER};` : ""}}
  img,video,svg,picture,canvas{${dark ? `filter:${DARK_FILTER};` : ""}max-width:100% !important;height:auto;}
  table{max-width:100%;}
  *{max-width:100%;box-sizing:border-box;}
  a{color:#0b8a67;}
  blockquote{margin:8px 0;padding-left:12px;border-left:3px solid #e2e2e2;color:#555;}
`;
}

function wrapEmail(bodyHtml: string, dark: boolean): string {
  return `<!doctype html><html><head><meta charset="utf-8"><base target="_blank"><style>${frameStyle(dark)}</style></head><body>${bodyHtml}</body></html>`;
}

// Style injected into a full, self-styled email (marketing) so it also goes dark.
function fullDocDark(): string {
  return `<style>html{background:#1b1c1e}body{filter:${DARK_FILTER}}img,video,svg,picture,canvas{filter:${DARK_FILTER}}</style>`;
}

// Split an HTML email into its new content and quoted history, the way Gmail/Outlook do.
function prepareEmail(rawHtml: string, dark: boolean): { visible: string; full: string; hasQuote: boolean } {
  const isFullDoc = /<html[\s>]/i.test(rawHtml) || /<!doctype/i.test(rawHtml);
  if (isFullDoc) {
    // Designed emails keep their own layout; add a base target and (in dark) the dark filter.
    const inject = `<base target="_blank">${dark ? fullDocDark() : ""}`;
    const out = /<head[^>]*>/i.test(rawHtml)
      ? rawHtml.replace(/<head[^>]*>/i, `$&${inject}`)
      : `<head>${inject}</head>${rawHtml}`;
    return { visible: out, full: out, hasQuote: false };
  }
  const doc = new DOMParser().parseFromString(rawHtml, "text/html");
  const quote = doc.querySelector(
    ".gmail_quote, .gmail_quote_container, blockquote[type='cite'], #appendonsend, #divRplyFwdMsg",
  );
  let hasQuote = false;
  if (quote) {
    hasQuote = true;
    // Outlook marks the divider with an empty node; drop it and everything after.
    let n: ChildNode | null = quote;
    while (n) {
      const next: ChildNode | null = n.nextSibling;
      n.remove();
      n = next;
    }
  }
  return { visible: wrapEmail(doc.body.innerHTML, dark), full: wrapEmail(rawHtml, dark), hasQuote };
}

function EmailFrame({ srcDoc, dark }: { srcDoc: string; dark: boolean }) {
  const ref = useRef<HTMLIFrameElement>(null);
  const [height, setHeight] = useState(48);
  const sync = () => {
    const d = ref.current?.contentDocument;
    if (d?.documentElement) setHeight(d.documentElement.scrollHeight);
  };
  return (
    <iframe
      ref={ref}
      title="Email message"
      sandbox="allow-same-origin allow-popups allow-popups-to-escape-sandbox"
      srcDoc={srcDoc}
      onLoad={() => {
        sync();
        setTimeout(sync, 300); // re-measure after images settle
      }}
      style={{
        width: "100%",
        height,
        border: 0,
        display: "block",
        background: dark ? "#1b1c1e" : "#ffffff",
        borderRadius: 6,
        colorScheme: dark ? "dark" : "light",
      }}
    />
  );
}

function EmailMessage({ html }: { html: string }) {
  const [showQuote, setShowQuote] = useState(false);
  const dark = useComputedColorScheme("light") === "dark";
  const { visible, full, hasQuote } = useMemo(() => prepareEmail(html, dark), [html, dark]);
  return (
    <>
      <EmailFrame key={`${showQuote ? "full" : "visible"}-${dark}`} srcDoc={showQuote ? full : visible} dark={dark} />
      {hasQuote && (
        <Button
          variant="subtle"
          color="gray"
          size="compact-xs"
          mt={6}
          leftSection={showQuote ? <IconChevronUp size={13} /> : <IconDots size={13} />}
          onClick={() => setShowQuote((s) => !s)}
        >
          {showQuote ? "Hide quoted text" : "Show quoted text"}
        </Button>
      )}
    </>
  );
}

function Thread({ messages }: { messages: TicketMessage[] }) {
  const bg = (m: TicketMessage) => BUBBLE_BG[m.direction] ?? BUBBLE_BG.inbound;
  return (
    <Stack>
      {messages.map((m) => (
        <Paper key={m.id} withBorder p="sm" style={{ backgroundColor: bg(m) }}>
          <Group justify="space-between" mb={4}>
            <Text size="sm" fw={600}>
              {m.direction === "note" ? "Internal note" : m.from_name || m.from_address}
              {m.direction === "outbound" && m.send_status !== "sent" && (
                <Badge ml="xs" color={m.send_status === "failed" ? "red" : "gray"} size="xs">
                  {m.send_status}
                </Badge>
              )}
            </Text>
            <Text size="xs" c="dimmed">
              {new Date(m.created_at).toLocaleString()}
            </Text>
          </Group>
          {m.direction !== "note" && m.body_html ? (
            <EmailMessage html={m.body_html} />
          ) : (
            <MessageBody text={m.body_text || m.snippet} />
          )}
          {m.attachments.length > 0 && (
            <Group gap="xs" mt="xs">
              {m.attachments.map((a) => (
                <Anchor key={a.id} href={`/api/v1/attachments/${a.id}`} size="xs">
                  <Group gap={4} wrap="nowrap" display="inline-flex">
                    <IconPaperclip size={13} />
                    {a.filename}
                  </Group>
                </Anchor>
              ))}
            </Group>
          )}
        </Paper>
      ))}
    </Stack>
  );
}

function Composer({
  ticket,
  reply,
  setReply,
  draftKey,
  onDone,
}: {
  ticket: TicketDetail;
  reply: string;
  setReply: (v: string) => void;
  draftKey: string;
  onDone: () => void;
}) {
  const queryClient = useQueryClient();
  const [note, setNote] = useState("");
  const canned = useCanned();
  // Snippets for this ticket's store, plus shared ones.
  const cannedOptions = (canned.data ?? [])
    .filter((c) => c.is_active && (c.store_id === null || c.store_id === ticket.store_id))
    .map((c) => ({ value: String(c.id), label: c.title }));
  const insertCanned = (id: string | null) => {
    const c = canned.data?.find((x) => String(x.id) === id);
    if (c) setReply(reply ? `${reply.trimEnd()}\n\n${c.body_text}` : c.body_text);
  };
  // One request id per compose session makes Send safe to retry (no duplicate email).
  const requestId = useMemo(() => crypto.randomUUID(), [ticket.id]);
  const lastSeen = useMemo(
    () => ticket.messages.filter((m) => m.direction === "inbound").map((m) => m.id).at(-1) ?? null,
    [ticket.messages],
  );

  const send = useMutation({
    mutationFn: (then: "wait" | "resolve") =>
      api(`/tickets/${ticket.id}/replies`, "POST", {
        body_text: reply,
        client_request_id: requestId,
        last_seen_message_id: lastSeen,
        then,
      }),
    onSuccess: () => {
      localStorage.removeItem(draftKey);
      setReply("");
      onDone();
      queryClient.invalidateQueries({ queryKey: ["tickets"] });
    },
  });
  const addNote = useMutation({
    mutationFn: () => api(`/tickets/${ticket.id}/notes`, "POST", { body_text: note }),
    onSuccess: () => {
      setNote("");
      onDone();
    },
  });

  const staleError = send.error instanceof ApiError && send.error.status === 409;

  return (
    <Card withBorder>
      <Tabs defaultValue="reply">
        <Tabs.List>
          <Tabs.Tab value="reply">Reply to customer</Tabs.Tab>
          <Tabs.Tab value="note">Internal note</Tabs.Tab>
        </Tabs.List>

        <Tabs.Panel value="reply" pt="sm">
          <Stack>
            {staleError && (
              <Alert color="orange" title="A newer message arrived">
                Reload to read it before replying. <Anchor onClick={onDone}>Reload</Anchor>
              </Alert>
            )}
            {send.isError && !staleError && <Alert color="red">{send.error.message}</Alert>}
            {cannedOptions.length > 0 && (
              <Select
                size="xs"
                maw={260}
                placeholder="Insert canned response…"
                leftSection={<IconMessages size={15} />}
                data={cannedOptions}
                value={null}
                searchable
                onChange={insertCanned}
              />
            )}
            <Textarea
              autosize
              minRows={5}
              placeholder="Write your reply…"
              value={reply}
              onChange={(e) => setReply(e.currentTarget.value)}
            />
            <Group justify="flex-end">
              <Button
                variant="default"
                disabled={!reply.trim()}
                loading={send.isPending}
                onClick={() => send.mutate("wait")}
              >
                Send
              </Button>
              <Button disabled={!reply.trim()} loading={send.isPending} onClick={() => send.mutate("resolve")}>
                Send &amp; resolve
              </Button>
            </Group>
          </Stack>
        </Tabs.Panel>

        <Tabs.Panel value="note" pt="sm">
          <Stack>
            <Textarea
              autosize
              minRows={3}
              placeholder="Note for your team (not sent to the customer)…"
              value={note}
              onChange={(e) => setNote(e.currentTarget.value)}
            />
            <Group justify="flex-end">
              <Button
                variant="light"
                disabled={!note.trim()}
                loading={addNote.isPending}
                onClick={() => addNote.mutate()}
              >
                Add note
              </Button>
            </Group>
          </Stack>
        </Tabs.Panel>
      </Tabs>
    </Card>
  );
}

const SENTIMENT_COLOR: Record<string, string> = { positive: "green", neutral: "gray", negative: "red" };

function SuggestionPanel({ ticketId, onUse }: { ticketId: number; onUse: (text: string) => void }) {
  const suggestion = useSuggestion(ticketId);
  const queryClient = useQueryClient();
  const rate = useMutation({
    mutationFn: (v: { sid: number; rating: number }) =>
      api(`/tickets/${ticketId}/suggestion/${v.sid}/rating`, "POST", { rating: v.rating }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["suggestion", ticketId] }),
  });

  const s: Suggestion | null | undefined = suggestion.data;
  if (!s) return null; // LLM off, or no customer message yet

  return (
    <Card withBorder bg="var(--mantine-color-brand-light)">
      <Group justify="space-between" mb="xs">
        <Group gap="xs">
          <ThemeIcon size="sm" radius="sm" variant="light" color="brand">
            <IconSparkles size={14} />
          </ThemeIcon>
          <Text fw={600}>Suggested reply</Text>
          <Badge size="xs" variant="light">
            AI draft — review before sending
          </Badge>
        </Group>
        {s.status === "ready" && (
          <Group gap={4}>
            <ActionButton
              active={s.rating === 1}
              label={<IconThumbUp size={15} />}
              onClick={() => rate.mutate({ sid: s.id, rating: s.rating === 1 ? 0 : 1 })}
            />
            <ActionButton
              active={s.rating === -1}
              label={<IconThumbDown size={15} />}
              onClick={() => rate.mutate({ sid: s.id, rating: s.rating === -1 ? 0 : -1 })}
            />
          </Group>
        )}
      </Group>

      {s.status === "pending" && (
        <Group gap="xs">
          <Loader size="xs" />
          <Text size="sm" c="dimmed">
            Drafting a suggestion…
          </Text>
        </Group>
      )}
      {s.status === "failed" && <Text size="sm" c="dimmed">No suggestion available for this message.</Text>}

      {s.status === "ready" && (
        <Stack gap="xs">
          {s.summary && (
            <Text size="sm" c="dimmed">
              {s.summary}
            </Text>
          )}
          <Group gap={6}>
            {s.category && <Badge size="xs" variant="outline">{s.category}</Badge>}
            {s.priority && <Badge size="xs" variant="outline">{s.priority}</Badge>}
            {s.sentiment && (
              <Badge size="xs" color={SENTIMENT_COLOR[s.sentiment] ?? "gray"}>
                {s.sentiment}
              </Badge>
            )}
          </Group>
          <Text size="sm" style={{ whiteSpace: "pre-wrap" }}>
            {s.suggested_reply}
          </Text>
          {s.missing_info.length > 0 && (
            <Alert color="yellow" p="xs">
              Still needed: {(s.missing_info as string[]).join("; ")}
            </Alert>
          )}
          <Group justify="space-between">
            <Text size="xs" c="dimmed">
              {s.documents_used.length
                ? `Based on: ${(s.documents_used as { title: string }[]).map((d) => d.title).join(", ")}`
                : "No store documents were used."}
            </Text>
            <Button size="xs" leftSection={<IconArrowBackUp size={15} />} onClick={() => onUse(s.suggested_reply)}>
              Use this draft
            </Button>
          </Group>
        </Stack>
      )}
    </Card>
  );
}

function ActionButton({ active, label, onClick }: { active: boolean; label: ReactNode; onClick: () => void }) {
  return (
    <ActionIcon size="md" radius="md" variant={active ? "filled" : "subtle"} color="brand" onClick={onClick}>
      {label}
    </ActionIcon>
  );
}

// The ticket controls and customer, as a horizontal bar above the full-width mail.
function MetaBar({
  ticket,
  meRole,
  meId,
  patch,
}: {
  ticket: TicketDetail;
  meRole: string | undefined;
  meId: number | undefined;
  patch: (body: object) => void;
}) {
  const stores = useStores();
  const canManage = meRole === "admin" || meRole === "manager";
  const users = useUsers(canManage);

  return (
    <Card withBorder>
      <Group align="flex-end" gap="md" wrap="wrap">
        <Select
          w={150}
          label="Status"
          data={STATUSES.map((s) => ({ value: s, label: s.replace("_", " ") }))}
          value={ticket.status}
          allowDeselect={false}
          onChange={(v) => v && patch({ status: v })}
        />
        <Select
          w={130}
          label="Priority"
          data={PRIORITIES}
          value={ticket.priority}
          allowDeselect={false}
          onChange={(v) => v && patch({ priority: v })}
        />
        <Select
          w={180}
          label="Store"
          data={(stores.data ?? []).map((s) => ({ value: String(s.id), label: s.name }))}
          value={String(ticket.store_id)}
          allowDeselect={false}
          onChange={(v) => v && patch({ store_id: Number(v) })}
        />
        <div>
          <Text size="sm" fw={500} mb={4}>
            Assignee
          </Text>
          {canManage && users.data ? (
            <Group gap="xs">
              <Select
                w={180}
                placeholder="Unassigned"
                data={users.data
                  .filter((u) => u.is_active)
                  .map((u) => ({ value: String(u.id), label: u.name || u.email }))}
                value={ticket.assignee_id ? String(ticket.assignee_id) : null}
                onChange={(v) => patch(v ? { assignee_id: Number(v) } : { clear_assignee: true })}
              />
              {meId && ticket.assignee_id !== meId && (
                <Button size="xs" variant="subtle" onClick={() => patch({ assignee_id: meId })}>
                  Assign to me
                </Button>
              )}
            </Group>
          ) : (
            <Text size="sm">{ticket.assignee_name ?? "Unassigned"}</Text>
          )}
        </div>

        <div style={{ marginLeft: "auto", textAlign: "right", minWidth: 0 }}>
          <Text size="xs" c="dimmed">
            Customer
          </Text>
          <Text size="sm" fw={500} truncate>
            {ticket.customer_name || ticket.customer_email}
          </Text>
          <Text size="xs" c="dimmed" truncate>
            {ticket.customer_email}
          </Text>
        </div>
      </Group>
    </Card>
  );
}

// Activity log and the customer's other open tickets, below the conversation.
function BottomInfo({ ticket }: { ticket: TicketDetail }) {
  return (
    <Group align="flex-start" grow wrap="wrap">
      <Card withBorder>
        <Text size="sm" fw={600} mb="xs">
          Activity
        </Text>
        <Stack gap={4}>
          {ticket.events.map((e) => (
            <Text key={e.id} size="xs" c="dimmed">
              {new Date(e.created_at).toLocaleString()} — {e.type.replace("_", " ")}
            </Text>
          ))}
        </Stack>
      </Card>
      {ticket.other_open_tickets.length > 0 && (
        <Card withBorder>
          <Text size="sm" fw={600} mb="xs">
            Other open tickets
          </Text>
          <Stack gap={4}>
            {ticket.other_open_tickets.map((o) => (
              <Anchor key={o.id} href={`/tickets/${o.id}`} size="sm">
                #{o.number} {o.subject}
              </Anchor>
            ))}
          </Stack>
        </Card>
      )}
    </Group>
  );
}
