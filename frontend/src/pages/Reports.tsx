import { Card, Group, Loader, SegmentedControl, Select, SimpleGrid, Stack, Table, Text, ThemeIcon, Title } from "@mantine/core";
import {
  IconChecks,
  IconClock,
  IconGauge,
  IconSparkles,
  IconThumbUp,
  IconTicket,
  type Icon,
} from "@tabler/icons-react";
import { useState } from "react";

import { useReport, useStores } from "../api";

function Stat({
  label,
  value,
  hint,
  icon: IconCmp,
  color,
}: {
  label: string;
  value: string;
  hint?: string;
  icon: Icon;
  color: string;
}) {
  return (
    <Card>
      <Group justify="space-between" align="flex-start" mb="xs">
        <Text size="sm" c="dimmed" fw={500}>
          {label}
        </Text>
        <ThemeIcon variant="light" color={color} radius="md" size="lg">
          <IconCmp size={19} stroke={1.7} />
        </ThemeIcon>
      </Group>
      <Text fz={30} fw={700} lh={1.1} style={{ fontVariantNumeric: "tabular-nums" }}>
        {value}
      </Text>
      {hint && (
        <Text size="xs" c="dimmed" mt={4}>
          {hint}
        </Text>
      )}
    </Card>
  );
}

export default function Reports() {
  const [days, setDays] = useState("30");
  const [storeId, setStoreId] = useState<string | null>(null);
  const stores = useStores();
  const resolvedStore = storeId && storeId !== "all" ? Number(storeId) : null;
  const report = useReport(Number(days), resolvedStore);

  const r = report.data;
  const accept =
    r && r.ai_helpful + r.ai_not_helpful > 0
      ? `${Math.round((100 * r.ai_helpful) / (r.ai_helpful + r.ai_not_helpful))}%`
      : "—";

  return (
    <Stack>
      <Group justify="space-between" wrap="wrap">
        <div>
          <Title order={3}>Reports</Title>
          <Text c="dimmed" size="sm">
            Volume, responsiveness, and AI assistance over time.
          </Text>
        </div>
        <Group>
          <Select
            w={200}
            data={[
              { value: "all", label: "All stores" },
              ...(stores.data ?? []).map((s) => ({ value: String(s.id), label: s.name })),
            ]}
            value={storeId ?? "all"}
            allowDeselect={false}
            onChange={setStoreId}
          />
          <SegmentedControl
            value={days}
            onChange={setDays}
            data={[
              { value: "7", label: "7 days" },
              { value: "30", label: "30 days" },
              { value: "90", label: "90 days" },
            ]}
          />
        </Group>
      </Group>

      {report.isPending || !r ? (
        <Loader color="brand" />
      ) : (
        <>
          <SimpleGrid cols={{ base: 1, sm: 2, lg: 4 }}>
            <Stat label="Tickets created" value={String(r.created)} icon={IconTicket} color="brand" />
            <Stat label="Tickets resolved" value={String(r.resolved)} icon={IconChecks} color="teal" />
            <Stat
              label="First-response SLA met"
              value={r.first_response_sla_pct === null ? "—" : `${r.first_response_sla_pct}%`}
              hint="share not breached"
              icon={IconGauge}
              color="green"
            />
            <Stat
              label="Median first response"
              value={r.median_first_response_min === null ? "—" : fmtMins(r.median_first_response_min)}
              hint="wall-clock, not business hours"
              icon={IconClock}
              color="blue"
            />
            <Stat
              label="Missed emails recovered"
              value={String(r.recoveries)}
              hint="by reconciliation"
              icon={IconChecks}
              color="indigo"
            />
            <Stat label="AI drafts generated" value={String(r.ai_ready)} icon={IconSparkles} color="grape" />
            <Stat
              label="AI drafts used"
              value={String(r.ai_used)}
              hint="sent after a draft existed"
              icon={IconSparkles}
              color="violet"
            />
            <Stat
              label="AI acceptance"
              value={accept}
              hint={`${r.ai_helpful} helpful · ${r.ai_not_helpful} not`}
              icon={IconThumbUp}
              color="yellow"
            />
          </SimpleGrid>

          <Title order={5} mt="md">
            By store
          </Title>
          <Card p={0}>
            <Table verticalSpacing="sm" horizontalSpacing="lg" highlightOnHover>
              <Table.Thead>
                <Table.Tr>
                  <Table.Th>Store</Table.Th>
                  <Table.Th ta="right">Created</Table.Th>
                  <Table.Th ta="right">Resolved</Table.Th>
                </Table.Tr>
              </Table.Thead>
              <Table.Tbody>
                {r.by_store.map((s) => (
                  <Table.Tr key={s.id}>
                    <Table.Td>{s.name}</Table.Td>
                    <Table.Td ta="right" style={{ fontVariantNumeric: "tabular-nums" }}>
                      {s.created}
                    </Table.Td>
                    <Table.Td ta="right" style={{ fontVariantNumeric: "tabular-nums" }}>
                      {s.resolved}
                    </Table.Td>
                  </Table.Tr>
                ))}
                {r.by_store.length === 0 && (
                  <Table.Tr>
                    <Table.Td colSpan={3}>
                      <Text c="dimmed">No tickets in this period.</Text>
                    </Table.Td>
                  </Table.Tr>
                )}
              </Table.Tbody>
            </Table>
          </Card>
        </>
      )}
    </Stack>
  );
}

function fmtMins(m: number): string {
  if (m < 60) return `${Math.round(m)} min`;
  const h = m / 60;
  if (h < 48) return `${h.toFixed(1)} h`;
  return `${(h / 24).toFixed(1)} d`;
}
