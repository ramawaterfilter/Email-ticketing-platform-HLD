import {
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
  Title,
} from "@mantine/core";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useState } from "react";
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
      <Group justify="space-between" align="flex-start">
        <div>
          <Title order={3}>#{t.number} · {t.subject || "(no subject)"}</Title>
          <Group gap="xs" mt={4}>
            <Badge variant="light">{t.store_name}</Badge>
            <Badge color="gray">{t.status.replace("_", " ")}</Badge>
            {t.delivery_failed && <Badge color="red">delivery failed</Badge>}
          </Group>
        </div>
      </Group>

      <Group align="flex-start" grow wrap="nowrap">
        <Stack style={{ flex: 2 }}>
          <Thread messages={t.messages} />
          <SuggestionPanel ticketId={t.id} onUse={(text) => setReply(text)} />
          <Composer ticket={t} reply={reply} setReply={setReply} draftKey={draftKey} onDone={refresh} />
        </Stack>
        <SidePanel ticket={t} meRole={me.data?.role} meId={me.data?.id} patch={patch.mutate} />
      </Group>
    </Stack>
  );
}

function Thread({ messages }: { messages: TicketMessage[] }) {
  const color = (m: TicketMessage) =>
    m.direction === "note" ? "yellow.0" : m.direction === "outbound" ? "blue.0" : "gray.0";
  return (
    <Stack>
      {messages.map((m) => (
        <Paper key={m.id} withBorder p="sm" bg={color(m)}>
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
          <Text size="sm" style={{ whiteSpace: "pre-wrap" }}>
            {m.body_text || m.snippet}
          </Text>
          {m.attachments.length > 0 && (
            <Group gap="xs" mt="xs">
              {m.attachments.map((a) => (
                <Anchor key={a.id} href={`/api/v1/attachments/${a.id}`} size="xs">
                  📎 {a.filename}
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
    <Card withBorder bg="var(--mantine-color-blue-light)">
      <Group justify="space-between" mb="xs">
        <Group gap="xs">
          <Text fw={600}>Suggested reply</Text>
          <Badge size="xs" variant="light">
            AI draft — review before sending
          </Badge>
        </Group>
        {s.status === "ready" && (
          <Group gap={4}>
            <ActionButton active={s.rating === 1} label="👍" onClick={() => rate.mutate({ sid: s.id, rating: s.rating === 1 ? 0 : 1 })} />
            <ActionButton active={s.rating === -1} label="👎" onClick={() => rate.mutate({ sid: s.id, rating: s.rating === -1 ? 0 : -1 })} />
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
            <Button size="xs" onClick={() => onUse(s.suggested_reply)}>
              Use this draft
            </Button>
          </Group>
        </Stack>
      )}
    </Card>
  );
}

function ActionButton({ active, label, onClick }: { active: boolean; label: string; onClick: () => void }) {
  return (
    <Button size="compact-xs" variant={active ? "filled" : "subtle"} onClick={onClick}>
      {label}
    </Button>
  );
}

function SidePanel({
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
    <Stack style={{ flex: 1, minWidth: 260 }}>
      <Card withBorder>
        <Stack gap="sm">
          <Select
            label="Status"
            data={STATUSES.map((s) => ({ value: s, label: s.replace("_", " ") }))}
            value={ticket.status}
            allowDeselect={false}
            onChange={(v) => v && patch({ status: v })}
          />
          <Select
            label="Priority"
            data={PRIORITIES}
            value={ticket.priority}
            allowDeselect={false}
            onChange={(v) => v && patch({ priority: v })}
          />
          <Select
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
              <Select
                placeholder="Unassigned"
                data={users.data
                  .filter((u) => u.is_active)
                  .map((u) => ({ value: String(u.id), label: u.name || u.email }))}
                value={ticket.assignee_id ? String(ticket.assignee_id) : null}
                onChange={(v) => patch(v ? { assignee_id: Number(v) } : { clear_assignee: true })}
              />
            ) : (
              <Text size="sm">{ticket.assignee_name ?? "Unassigned"}</Text>
            )}
            {meId && ticket.assignee_id !== meId && (
              <Button size="xs" variant="subtle" mt={6} onClick={() => patch({ assignee_id: meId })}>
                Assign to me
              </Button>
            )}
          </div>
        </Stack>
      </Card>

      <Card withBorder>
        <Text size="sm" fw={600}>
          Customer
        </Text>
        <Text size="sm">{ticket.customer_name || ticket.customer_email}</Text>
        <Text size="xs" c="dimmed">
          {ticket.customer_email}
        </Text>
        {ticket.other_open_tickets.length > 0 && (
          <>
            <Divider my="xs" />
            <Text size="xs" c="dimmed" mb={4}>
              Other open tickets
            </Text>
            {ticket.other_open_tickets.map((o) => (
              <Anchor key={o.id} href={`/tickets/${o.id}`} size="sm" display="block">
                #{o.number} {o.subject}
              </Anchor>
            ))}
          </>
        )}
      </Card>

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
    </Stack>
  );
}
