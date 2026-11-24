import {
  Alert,
  Button,
  Card,
  Group,
  SegmentedControl,
  Select,
  Stack,
  Switch,
  Table,
  Text,
  Title,
} from "@mantine/core";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";

import { api } from "../api";

type AssignmentSettings = { enabled: boolean; strategy: "least_loaded" | "round_robin" };
type ExecRow = {
  id: number;
  name: string;
  email: string;
  assigned: number;
  resolved: number;
  open: number;
  reopened: number;
};
type ExecPerformance = { from_date: string; to_date: string; rows: ExecRow[] };

export default function Team() {
  return (
    <Stack>
      <Title order={3}>Team</Title>
      <AssignmentCard />
      <PerformanceCard />
    </Stack>
  );
}

function AssignmentCard() {
  const queryClient = useQueryClient();
  const current = useQuery({
    queryKey: ["assignment"],
    queryFn: () => api<AssignmentSettings>("/settings/assignment"),
  });
  const [enabled, setEnabled] = useState(true);
  const [strategy, setStrategy] = useState<AssignmentSettings["strategy"]>("least_loaded");
  useEffect(() => {
    if (current.data) {
      setEnabled(current.data.enabled);
      setStrategy(current.data.strategy);
    }
  }, [current.data]);

  const save = useMutation({
    mutationFn: () => api("/settings/assignment", "PUT", { enabled, strategy }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["assignment"] }),
  });

  return (
    <Card withBorder>
      <Stack gap="sm">
        <Text fw={600}>Auto-assignment</Text>
        <Switch
          label="Assign new tickets automatically"
          description="New customer tickets are handed to an available executive. Turn off to leave everything unassigned for manual pickup."
          checked={enabled}
          onChange={(e) => setEnabled(e.currentTarget.checked)}
        />
        {enabled && (
          <div>
            <Text size="sm" fw={500}>
              Strategy
            </Text>
            <Text size="xs" c="dimmed" mb={6}>
              Both respect each person's open-ticket cap, availability and store membership (set on the People screen).
            </Text>
            <SegmentedControl
              value={strategy}
              onChange={(v) => setStrategy(v as AssignmentSettings["strategy"])}
              data={[
                { value: "least_loaded", label: "Least loaded" },
                { value: "round_robin", label: "Round robin" },
              ]}
            />
            <Text size="xs" c="dimmed" mt={6}>
              {strategy === "least_loaded"
                ? "Picks whoever has the fewest open tickets right now."
                : "Rotates evenly — picks whoever was assigned longest ago."}
            </Text>
          </div>
        )}
        {save.isError && <Alert color="red">{(save.error as Error).message}</Alert>}
        <Group justify="flex-end">
          <Button loading={save.isPending} onClick={() => save.mutate()}>
            Save
          </Button>
        </Group>
      </Stack>
    </Card>
  );
}

function PerformanceCard() {
  const [days, setDays] = useState("30");
  const perf = useQuery({
    queryKey: ["exec-perf", days],
    queryFn: () => api<ExecPerformance>(`/reports/executives?days=${days}`),
  });

  return (
    <Card withBorder>
      <Group justify="space-between" mb="sm">
        <Text fw={600}>Executive performance</Text>
        <Select
          size="xs"
          w={140}
          value={days}
          onChange={(v) => v && setDays(v)}
          data={[
            { value: "7", label: "Last 7 days" },
            { value: "30", label: "Last 30 days" },
            { value: "90", label: "Last 90 days" },
          ]}
          allowDeselect={false}
        />
      </Group>
      <Table highlightOnHover>
        <Table.Thead>
          <Table.Tr>
            <Table.Th>Executive</Table.Th>
            <Table.Th ta="right">Assigned</Table.Th>
            <Table.Th ta="right">Resolved</Table.Th>
            <Table.Th ta="right">Open now</Table.Th>
            <Table.Th ta="right">Reopened</Table.Th>
          </Table.Tr>
        </Table.Thead>
        <Table.Tbody>
          {perf.data?.rows.map((r) => (
            <Table.Tr key={r.id}>
              <Table.Td>
                <Text>{r.name}</Text>
                <Text size="xs" c="dimmed">
                  {r.email}
                </Text>
              </Table.Td>
              <Table.Td ta="right">{r.assigned}</Table.Td>
              <Table.Td ta="right">{r.resolved}</Table.Td>
              <Table.Td ta="right">{r.open}</Table.Td>
              <Table.Td ta="right">{r.reopened}</Table.Td>
            </Table.Tr>
          ))}
          {perf.data?.rows.length === 0 && (
            <Table.Tr>
              <Table.Td colSpan={5}>
                <Text c="dimmed">No executives yet.</Text>
              </Table.Td>
            </Table.Tr>
          )}
        </Table.Tbody>
      </Table>
    </Card>
  );
}
