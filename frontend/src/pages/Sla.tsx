import { Alert, Button, Card, Group, NumberInput, Stack, Switch, Table, Text, Textarea, TextInput, Title } from "@mantine/core";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";

import { api, type BusinessHours, type SlaPolicy, useStores } from "../api";

const DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"];

export default function Sla() {
  return (
    <Stack>
      <Title order={3}>SLA &amp; alerts</Title>
      <Policies />
      <Hours />
      <AlertsCard />
    </Stack>
  );
}

function Policies() {
  const stores = useStores();
  const queryClient = useQueryClient();
  const policies = useQuery({ queryKey: ["sla-policies"], queryFn: () => api<SlaPolicy[]>("/sla-policies") });
  const save = useMutation({
    mutationFn: (p: SlaPolicy) => api("/sla-policies", "PUT", p),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["sla-policies"] }),
  });
  const enabled = useQuery({ queryKey: ["sla-enabled"], queryFn: () => api<{ enabled: boolean }>("/settings/sla-enabled") });
  const toggle = useMutation({
    mutationFn: (v: boolean) => api("/settings/sla-enabled", "PUT", { enabled: v }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["sla-enabled"] }),
  });
  const on = enabled.data?.enabled ?? true;

  const byStore = new Map(policies.data?.map((p) => [p.store_id, p]));
  const rows: { store_id: number | null; name: string }[] = [
    { store_id: null, name: "Default (all stores)" },
    ...(stores.data ?? []).map((s) => ({ store_id: s.id, name: s.name })),
  ];

  return (
    <Card withBorder>
      <Group justify="space-between" mb="xs">
        <Text fw={600}>Response targets (business minutes)</Text>
        <Switch
          checked={on}
          onChange={(e) => toggle.mutate(e.currentTarget.checked)}
          disabled={enabled.isLoading || toggle.isPending}
          label={on ? "SLA on" : "SLA off"}
        />
      </Group>
      {!on && (
        <Alert color="gray" mb="xs">
          SLA tracking is off — tickets get no reply/resolution timers and no breach alerts fire. Turn it back on to resume.
        </Alert>
      )}
      <Table>
        <Table.Thead>
          <Table.Tr>
            <Table.Th>Scope</Table.Th>
            <Table.Th>First reply</Table.Th>
            <Table.Th>Follow-up reply</Table.Th>
            <Table.Th>Resolution</Table.Th>
            <Table.Th />
          </Table.Tr>
        </Table.Thead>
        <Table.Tbody>
          {rows.map((r) => (
            <PolicyRow
              key={String(r.store_id)}
              name={r.name}
              policy={byStore.get(r.store_id) ?? {
                store_id: r.store_id,
                first_response_min: 240,
                next_response_min: 480,
                resolution_min: 2880,
              }}
              saving={save.isPending}
              disabled={!on}
              onSave={save.mutate}
            />
          ))}
        </Table.Tbody>
      </Table>
    </Card>
  );
}

function PolicyRow({ name, policy, saving, disabled, onSave }: {
  name: string;
  policy: SlaPolicy;
  saving: boolean;
  disabled: boolean;
  onSave: (p: SlaPolicy) => void;
}) {
  const [p, setP] = useState(policy);
  useEffect(() => setP(policy), [policy]);
  return (
    <Table.Tr>
      <Table.Td>{name}</Table.Td>
      {(["first_response_min", "next_response_min", "resolution_min"] as const).map((f) => (
        <Table.Td key={f}>
          <NumberInput w={110} min={5} disabled={disabled} value={p[f]} onChange={(v) => setP({ ...p, [f]: Number(v) || 5 })} />
        </Table.Td>
      ))}
      <Table.Td>
        <Button size="xs" loading={saving} disabled={disabled} onClick={() => onSave(p)}>
          Save
        </Button>
      </Table.Td>
    </Table.Tr>
  );
}

function Hours() {
  const queryClient = useQueryClient();
  const hours = useQuery({ queryKey: ["business-hours"], queryFn: () => api<BusinessHours>("/settings/business-hours") });
  const [draft, setDraft] = useState<BusinessHours | null>(null);
  useEffect(() => setDraft(hours.data ?? null), [hours.data]);

  const save = useMutation({
    mutationFn: (b: BusinessHours) => api("/settings/business-hours", "PUT", b),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["business-hours"] }),
  });

  if (!draft) return null;
  const windowFor = (day: string): [string, string] | null => {
    const w = draft.week[day];
    return w && w[0] ? [w[0][0], w[0][1]] : null;
  };
  const setDay = (day: string, open: [string, string] | null) =>
    setDraft({ ...draft, week: { ...draft.week, [day]: open ? [open] : [] } });

  return (
    <Card withBorder>
      <Text fw={600} mb="xs">
        Business hours
      </Text>
      {save.isError && <Alert color="red">{save.error.message}</Alert>}
      <Stack gap="xs">
        <TextInput
          label="Timezone"
          description="e.g. Asia/Kolkata"
          w={260}
          value={draft.timezone}
          onChange={(e) => setDraft({ ...draft, timezone: e.currentTarget.value })}
        />
        {DAYS.map((day) => {
          const w = windowFor(day);
          return (
            <Group key={day} gap="sm">
              <Switch
                w={70}
                label={day}
                checked={!!w}
                onChange={(e) => setDay(day, e.currentTarget.checked ? ["10:00", "19:00"] : null)}
              />
              {w && (
                <>
                  <TextInput w={90} value={w[0]} onChange={(e) => setDay(day, [e.currentTarget.value, w[1]])} />
                  <Text>to</Text>
                  <TextInput w={90} value={w[1]} onChange={(e) => setDay(day, [w[0], e.currentTarget.value])} />
                </>
              )}
            </Group>
          );
        })}
        <Textarea
          label="Holidays"
          description="One date per line, e.g. 2026-12-25"
          value={(draft.holidays ?? []).join("\n")}
          onChange={(e) =>
            setDraft({ ...draft, holidays: e.currentTarget.value.split("\n").map((s) => s.trim()).filter(Boolean) })
          }
        />
        <Group justify="flex-end">
          <Button loading={save.isPending} onClick={() => save.mutate(draft)}>
            Save hours
          </Button>
        </Group>
      </Stack>
    </Card>
  );
}

function AlertsCard() {
  const queryClient = useQueryClient();
  const alerts = useQuery({ queryKey: ["alert-settings"], queryFn: () => api<{ manager_emails: string[]; google_chat_webhook: string | null }>("/settings/alerts") });
  const [emails, setEmails] = useState("");
  const [webhook, setWebhook] = useState("");
  useEffect(() => {
    if (alerts.data) {
      setEmails(alerts.data.manager_emails.join("\n"));
      setWebhook(alerts.data.google_chat_webhook ?? "");
    }
  }, [alerts.data]);

  const save = useMutation({
    mutationFn: () =>
      api("/settings/alerts", "PUT", {
        manager_emails: emails.split("\n").map((s) => s.trim()).filter(Boolean),
        google_chat_webhook: webhook.trim() || null,
      }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["alert-settings"] }),
  });

  return (
    <Card withBorder>
      <Text fw={600} mb="xs">
        Alert recipients
      </Text>
      <Stack gap="xs">
        <Textarea
          label="Manager emails for breach alerts"
          description="One per line. Managers and admins are always included."
          value={emails}
          onChange={(e) => setEmails(e.currentTarget.value)}
        />
        <TextInput
          label="Google Chat webhook (optional)"
          value={webhook}
          onChange={(e) => setWebhook(e.currentTarget.value)}
        />
        <Group justify="flex-end">
          <Button loading={save.isPending} onClick={() => save.mutate()}>
            Save alerts
          </Button>
        </Group>
      </Stack>
    </Card>
  );
}
