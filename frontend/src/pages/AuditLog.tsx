import { Badge, Code, Group, Stack, Table, Text, Title } from "@mantine/core";
import { useQuery } from "@tanstack/react-query";

import { api } from "../api";

type AuditEntry = {
  id: number;
  actor_user_id: number | null;
  actor_email: string | null;
  action: string;
  target_type: string | null;
  target_id: string | null;
  summary: string;
  data: Record<string, unknown>;
  created_at: string;
};

const actionColor = (action: string) => {
  if (action.endsWith(".delete")) return "red";
  if (action.endsWith(".create")) return "green";
  return "blue";
};

export default function AuditLog() {
  const log = useQuery({ queryKey: ["audit"], queryFn: () => api<AuditEntry[]>("/audit") });

  return (
    <Stack>
      <Title order={3}>Activity log</Title>
      <Text c="dimmed" size="sm">
        A record of critical changes — settings, users, stores and the knowledge base — with who made them and when.
      </Text>

      <Table highlightOnHover>
        <Table.Thead>
          <Table.Tr>
            <Table.Th>When</Table.Th>
            <Table.Th>Who</Table.Th>
            <Table.Th>Action</Table.Th>
            <Table.Th>Details</Table.Th>
          </Table.Tr>
        </Table.Thead>
        <Table.Tbody>
          {log.data?.map((e) => (
            <Table.Tr key={e.id}>
              <Table.Td style={{ whiteSpace: "nowrap" }}>
                <Text size="sm">{new Date(e.created_at).toLocaleString()}</Text>
              </Table.Td>
              <Table.Td>
                <Text size="sm">{e.actor_email ?? "system"}</Text>
              </Table.Td>
              <Table.Td>
                <Badge variant="light" color={actionColor(e.action)}>
                  {e.action}
                </Badge>
              </Table.Td>
              <Table.Td>
                <Text size="sm">{e.summary}</Text>
                {Object.keys(e.data ?? {}).length > 0 && (
                  <Code block mt={4} style={{ fontSize: 11 }}>
                    {JSON.stringify(e.data)}
                  </Code>
                )}
              </Table.Td>
            </Table.Tr>
          ))}
          {log.data?.length === 0 && (
            <Table.Tr>
              <Table.Td colSpan={4}>
                <Text c="dimmed">No activity recorded yet.</Text>
              </Table.Td>
            </Table.Tr>
          )}
        </Table.Tbody>
      </Table>
      {log.data && log.data.length >= 200 && (
        <Group justify="center">
          <Text c="dimmed" size="xs">
            Showing the 200 most recent entries.
          </Text>
        </Group>
      )}
    </Stack>
  );
}
