import { Badge, Card, Group, Loader, SimpleGrid, Stack, Table, Text, Title } from "@mantine/core";
import { useNavigate } from "react-router";

import { useDashboard } from "../api";

const STATUS_COLOR: Record<string, string> = { active: "green", pending: "yellow", error: "red", disabled: "gray" };

function Tile({ label, value, color, onClick }: { label: string; value: number; color: string; onClick?: () => void }) {
  return (
    <Card withBorder style={{ cursor: onClick ? "pointer" : "default" }} onClick={onClick}>
      <Text size="xs" c="dimmed" tt="uppercase">
        {label}
      </Text>
      <Text fw={700} size="32px" c={value > 0 ? color : undefined}>
        {value}
      </Text>
    </Card>
  );
}

export default function Board() {
  const board = useDashboard();
  const navigate = useNavigate();
  if (board.isPending) return <Loader />;
  if (board.isError || !board.data) return <Text c="red">Could not load the board.</Text>;
  const d = board.data;

  return (
    <Stack>
      <Title order={3}>Manager board</Title>

      <SimpleGrid cols={{ base: 1, sm: 3 }}>
        <Tile label="Unassigned" value={d.unassigned} color="orange" onClick={() => navigate("/?tab=unassigned")} />
        <Tile label="Due soon" value={d.due_soon} color="yellow" />
        <Tile label="Breached" value={d.breached} color="red" />
      </SimpleGrid>

      <SimpleGrid cols={{ base: 1, md: 2 }}>
        <Card withBorder>
          <Text fw={600} mb="xs">
            Open tickets by executive
          </Text>
          <Table>
            <Table.Tbody>
              {d.by_executive.map((e) => (
                <Table.Tr key={e.id}>
                  <Table.Td>{e.name}</Table.Td>
                  <Table.Td ta="right">{e.open_count}</Table.Td>
                </Table.Tr>
              ))}
              {d.by_executive.length === 0 && (
                <Table.Tr>
                  <Table.Td>
                    <Text c="dimmed">No assigned tickets.</Text>
                  </Table.Td>
                </Table.Tr>
              )}
            </Table.Tbody>
          </Table>
        </Card>

        <Card withBorder>
          <Text fw={600} mb="xs">
            Open tickets by store
          </Text>
          <Table>
            <Table.Tbody>
              {d.by_store.map((s) => (
                <Table.Tr key={s.id}>
                  <Table.Td>{s.name}</Table.Td>
                  <Table.Td ta="right">{s.open_count}</Table.Td>
                </Table.Tr>
              ))}
            </Table.Tbody>
          </Table>
        </Card>
      </SimpleGrid>

      <Card withBorder>
        <Text fw={600} mb="xs">
          Mailbox sync health
        </Text>
        <Table>
          <Table.Thead>
            <Table.Tr>
              <Table.Th>Mailbox</Table.Th>
              <Table.Th>Status</Table.Th>
              <Table.Th>Last sync</Table.Th>
              <Table.Th>Error</Table.Th>
            </Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            {d.mailboxes.map((m) => (
              <Table.Tr key={m.id}>
                <Table.Td>{m.google_email}</Table.Td>
                <Table.Td>
                  <Badge color={STATUS_COLOR[m.status] ?? "gray"}>{m.status}</Badge>
                </Table.Td>
                <Table.Td>{m.last_sync_at ? new Date(m.last_sync_at).toLocaleString() : "never"}</Table.Td>
                <Table.Td>
                  {m.last_sync_error && (
                    <Text c="red" size="xs">
                      {m.last_sync_error}
                    </Text>
                  )}
                </Table.Td>
              </Table.Tr>
            ))}
            {d.mailboxes.length === 0 && (
              <Table.Tr>
                <Table.Td colSpan={4}>
                  <Text c="dimmed">No mailboxes connected.</Text>
                </Table.Td>
              </Table.Tr>
            )}
          </Table.Tbody>
        </Table>
      </Card>
    </Stack>
  );
}
