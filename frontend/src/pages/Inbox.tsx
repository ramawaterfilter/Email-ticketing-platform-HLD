import { Badge, Group, Loader, SegmentedControl, Stack, Table, Tabs, Text, TextInput, Title } from "@mantine/core";
import { useState } from "react";
import { useNavigate } from "react-router";

import { type TicketListItem, useTickets } from "../api";
import { useActiveStore } from "../activeStore";

const TABS = [
  { value: "mine", label: "Mine", query: "view=mine&queue=customer" },
  { value: "unassigned", label: "Unassigned", query: "view=unassigned&queue=customer" },
  { value: "all", label: "All", query: "view=all&queue=customer" },
  { value: "other", label: "Other", query: "queue=other" },
  { value: "spam", label: "Spam review", query: "queue=spam" },
];

const PRIORITY_COLOR: Record<string, string> = { low: "gray", normal: "blue", high: "orange", urgent: "red" };

function fmt(mins: number): string {
  const a = Math.abs(mins);
  if (a < 60) return `${a}m`;
  const hrs = Math.round(a / 60);
  return hrs < 24 ? `${hrs}h` : `${Math.round(hrs / 24)}d`;
}

function dueStatus(t: TicketListItem): { text: string; color: string } {
  if (t.breached) return { text: "overdue", color: "red" };
  if (!t.reply_due_at) return { text: "", color: "gray" };
  const mins = Math.round((new Date(t.reply_due_at).getTime() - Date.now()) / 60000);
  if (mins < 0) return { text: "overdue", color: "red" };
  if (mins < 60) return { text: `due ${fmt(mins)}`, color: "orange" };
  return { text: `due ${fmt(mins)}`, color: "green" };
}

export default function Inbox() {
  const [tab, setTab] = useState("mine");
  const [search, setSearch] = useState("");
  const [sla, setSla] = useState("all");
  const { storeId } = useActiveStore();
  const active = TABS.find((t) => t.value === tab)!;
  let query = active.query;
  if (search.trim()) query += `&q=${encodeURIComponent(search.trim())}`;
  if (sla !== "all") query += `&sla=${sla}`;
  if (storeId) query += `&store_id=${storeId}`;
  const tickets = useTickets(query);

  return (
    <Stack>
      <Group justify="space-between">
        <Title order={3}>Tickets</Title>
        <TextInput
          placeholder="Search subject, email or #number"
          value={search}
          onChange={(e) => setSearch(e.currentTarget.value)}
          w={320}
        />
      </Group>

      <Group justify="space-between">
        <Tabs value={tab} onChange={(v) => v && setTab(v)}>
          <Tabs.List>
            {TABS.map((t) => (
              <Tabs.Tab key={t.value} value={t.value}>
                {t.label}
              </Tabs.Tab>
            ))}
          </Tabs.List>
        </Tabs>
        {["mine", "unassigned", "all"].includes(tab) && (
          <SegmentedControl
            size="xs"
            value={sla}
            onChange={setSla}
            data={[
              { value: "all", label: "All" },
              { value: "due_soon", label: "Due soon" },
              { value: "breached", label: "Breached" },
            ]}
          />
        )}
      </Group>

      {tickets.isPending && <Loader />}
      {tickets.data?.items.length === 0 && <Text c="dimmed">No tickets here.</Text>}
      {!!tickets.data?.items.length && (
        <Table highlightOnHover>
          <Table.Thead>
            <Table.Tr>
              <Table.Th>#</Table.Th>
              <Table.Th>Subject</Table.Th>
              <Table.Th>Customer</Table.Th>
              <Table.Th>Store</Table.Th>
              <Table.Th>Status</Table.Th>
              <Table.Th>Assignee</Table.Th>
              <Table.Th>SLA</Table.Th>
            </Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            {tickets.data.items.map((t) => (
              <Row key={t.id} t={t} />
            ))}
          </Table.Tbody>
        </Table>
      )}
    </Stack>
  );
}

function Row({ t }: { t: TicketListItem }) {
  const navigate = useNavigate();
  const due = dueStatus(t);
  return (
    <Table.Tr onClick={() => navigate(`/tickets/${t.id}`)} style={{ cursor: "pointer" }}>
      <Table.Td>#{t.number}</Table.Td>
      <Table.Td>
        <Group gap={6}>
          <Text lineClamp={1}>{t.subject || "(no subject)"}</Text>
          {t.delivery_failed && (
            <Badge color="red" size="xs">
              delivery failed
            </Badge>
          )}
          <Badge color={PRIORITY_COLOR[t.priority]} size="xs" variant="light">
            {t.priority}
          </Badge>
        </Group>
      </Table.Td>
      <Table.Td>{t.customer_name || t.customer_email}</Table.Td>
      <Table.Td>
        <Badge variant="light">{t.store_name}</Badge>
      </Table.Td>
      <Table.Td>{t.status.replace("_", " ")}</Table.Td>
      <Table.Td>{t.assignee_name ?? <Text c="dimmed">—</Text>}</Table.Td>
      <Table.Td>{due.text && <Badge color={due.color} variant="light">{due.text}</Badge>}</Table.Td>
    </Table.Tr>
  );
}
