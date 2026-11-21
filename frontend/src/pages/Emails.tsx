import { Alert, Badge, Button, Group, NumberInput, Select, Stack, Table, Text, Title } from "@mantine/core";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { api, type MailboxDetail, type SupportAddress, useMailbox, useMailboxes, useStores } from "../api";

const STATUS_COLOR: Record<string, string> = {
  active: "green",
  pending: "yellow",
  error: "red",
  disabled: "gray",
};

function timeAgo(iso: string | null): string {
  if (!iso) return "never";
  const mins = Math.round((Date.now() - new Date(iso).getTime()) / 60000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins} min ago`;
  const hrs = Math.round(mins / 60);
  return hrs < 24 ? `${hrs} h ago` : `${Math.round(hrs / 24)} d ago`;
}

export default function Emails() {
  const mailboxes = useMailboxes();

  return (
    <Stack>
      <Group justify="space-between">
        <Title order={3}>Email accounts</Title>
        <Button component="a" href="/api/v1/mailboxes/connect">
          Connect email account
        </Button>
      </Group>
      <Text c="dimmed" size="sm">
        Each address below must be assigned to a store. Tickets take their store from the address the email arrived on.
      </Text>
      {mailboxes.isError && <Alert color="red">Could not load email accounts.</Alert>}
      {mailboxes.data?.length === 0 && (
        <Alert color="blue">No email accounts connected yet. Connect one to start receiving tickets.</Alert>
      )}
      {mailboxes.data?.map((m) => <MailboxCard key={m.id} id={m.id} />)}
    </Stack>
  );
}

function MailboxCard({ id }: { id: number }) {
  const mailbox = useMailbox(id);
  const stores = useStores();
  const queryClient = useQueryClient();
  const [backfillDays, setBackfillDays] = useState(7);
  const refresh = () => {
    queryClient.invalidateQueries({ queryKey: ["mailbox", id] });
    queryClient.invalidateQueries({ queryKey: ["mailboxes"] });
  };

  const setStore = useMutation({
    mutationFn: (v: { addressId: number; storeId: number | null }) =>
      api<SupportAddress>(`/support-addresses/${v.addressId}`, "PATCH", { store_id: v.storeId }),
    onSuccess: refresh,
  });
  const activate = useMutation({
    mutationFn: (body: { status: "active" | "disabled"; backfill_days?: number }) =>
      api(`/mailboxes/${id}`, "PATCH", body),
    onSuccess: refresh,
  });
  const reconcile = useMutation({
    mutationFn: () => api(`/mailboxes/${id}/reconcile`, "POST"),
    onSuccess: refresh,
  });

  if (!mailbox.data) return null;
  const m: MailboxDetail = mailbox.data;
  const storeOptions = (stores.data ?? []).map((s) => ({ value: String(s.id), label: s.name }));
  const defaultUnmapped = m.addresses.some((a) => a.is_default && a.store_id === null);

  return (
    <Stack gap="xs" p="md" style={{ border: "1px solid var(--mantine-color-gray-3)", borderRadius: 8 }}>
      <Group justify="space-between">
        <Group gap="sm">
          <Text fw={600}>{m.google_email}</Text>
          <Badge color={STATUS_COLOR[m.status] ?? "gray"}>{m.status}</Badge>
        </Group>
        <Group gap="xs">
          {m.status === "pending" && (
            <NumberInput
              size="xs"
              w={150}
              min={1}
              max={90}
              value={backfillDays}
              onChange={(v) => setBackfillDays(Number(v) || 7)}
              label="Import last N days"
            />
          )}
          {m.status === "pending" && (
            <Button
              size="xs"
              disabled={defaultUnmapped}
              loading={activate.isPending}
              onClick={() => activate.mutate({ status: "active", backfill_days: backfillDays })}
            >
              Turn on syncing
            </Button>
          )}
          {m.status === "active" && (
            <>
              <Button size="xs" variant="default" loading={reconcile.isPending} onClick={() => reconcile.mutate()}>
                Run check now
              </Button>
              <Button size="xs" variant="subtle" color="red" onClick={() => activate.mutate({ status: "disabled" })}>
                Pause
              </Button>
            </>
          )}
        </Group>
      </Group>

      {defaultUnmapped && m.status === "pending" && (
        <Alert color="yellow" p="xs">
          Assign the default address to a store before turning on syncing.
        </Alert>
      )}
      {activate.isError && <Alert color="red" p="xs">{activate.error.message}</Alert>}

      <Table>
        <Table.Thead>
          <Table.Tr>
            <Table.Th>Address</Table.Th>
            <Table.Th>Store</Table.Th>
            <Table.Th />
          </Table.Tr>
        </Table.Thead>
        <Table.Tbody>
          {m.addresses.map((a) => (
            <Table.Tr key={a.id}>
              <Table.Td>
                {a.address} {a.is_default && <Badge size="xs" variant="light">default</Badge>}
              </Table.Td>
              <Table.Td>
                <Select
                  placeholder="Choose a store"
                  data={storeOptions}
                  value={a.store_id ? String(a.store_id) : null}
                  onChange={(v) => setStore.mutate({ addressId: a.id, storeId: v ? Number(v) : null })}
                  w={240}
                />
              </Table.Td>
              <Table.Td>{a.store_id === null && <Text c="red" size="sm">Unassigned</Text>}</Table.Td>
            </Table.Tr>
          ))}
        </Table.Tbody>
      </Table>

      <Group gap="lg">
        <Text size="xs" c="dimmed">Last sync: {timeAgo(m.last_sync_at)}</Text>
        {m.last_reconcile && (
          <Text size="xs" c={m.last_reconcile.recovered_count ? "red" : "dimmed"}>
            Last check: recovered {m.last_reconcile.recovered_count}
          </Text>
        )}
        {m.last_sync_error && <Text size="xs" c="red">Error: {m.last_sync_error}</Text>}
      </Group>
    </Stack>
  );
}
