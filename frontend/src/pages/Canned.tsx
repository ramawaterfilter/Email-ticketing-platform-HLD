import {
  Alert,
  Badge,
  Button,
  Group,
  Modal,
  Select,
  Stack,
  Table,
  Text,
  Textarea,
  TextInput,
  Title,
} from "@mantine/core";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { type FormEvent, useState } from "react";

import { api, type CannedResponse, useStores } from "../api";

export default function Canned() {
  const queryClient = useQueryClient();
  const items = useQuery({ queryKey: ["canned"], queryFn: () => api<CannedResponse[]>("/canned") });
  const stores = useStores();
  const [adding, setAdding] = useState(false);
  const refresh = () => queryClient.invalidateQueries({ queryKey: ["canned"] });

  const storeName = (id: number | null) =>
    id === null ? "Shared (all stores)" : stores.data?.find((s) => s.id === id)?.name ?? id;
  const toggle = useMutation({
    mutationFn: (c: CannedResponse) => api(`/canned/${c.id}`, "PATCH", { is_active: !c.is_active }),
    onSuccess: refresh,
  });
  const remove = useMutation({ mutationFn: (id: number) => api(`/canned/${id}`, "DELETE"), onSuccess: refresh });

  return (
    <Stack>
      <Group justify="space-between">
        <Title order={3}>Canned responses</Title>
        <Button onClick={() => setAdding(true)}>Add response</Button>
      </Group>
      <Text c="dimmed" size="sm">
        Saved reply snippets executives can insert while replying. Tag each to a store, or mark it shared.
      </Text>

      <Table>
        <Table.Thead>
          <Table.Tr>
            <Table.Th>Title</Table.Th>
            <Table.Th>Scope</Table.Th>
            <Table.Th>Preview</Table.Th>
            <Table.Th>Status</Table.Th>
            <Table.Th />
          </Table.Tr>
        </Table.Thead>
        <Table.Tbody>
          {items.data?.map((c) => (
            <Table.Tr key={c.id}>
              <Table.Td>{c.title}</Table.Td>
              <Table.Td>
                <Badge variant="light" color={c.store_id === null ? "grape" : "blue"}>
                  {storeName(c.store_id)}
                </Badge>
              </Table.Td>
              <Table.Td>
                <Text size="xs" c="dimmed" lineClamp={1} maw={320}>
                  {c.body_text}
                </Text>
              </Table.Td>
              <Table.Td>
                <Badge color={c.is_active ? "green" : "gray"}>{c.is_active ? "Active" : "Off"}</Badge>
              </Table.Td>
              <Table.Td ta="right">
                <Button size="compact-xs" variant="subtle" onClick={() => toggle.mutate(c)}>
                  {c.is_active ? "Turn off" : "Turn on"}
                </Button>
                <Button size="compact-xs" variant="subtle" color="red" onClick={() => remove.mutate(c.id)}>
                  Delete
                </Button>
              </Table.Td>
            </Table.Tr>
          ))}
          {items.data?.length === 0 && (
            <Table.Tr>
              <Table.Td colSpan={5}>
                <Text c="dimmed">No canned responses yet. Add your common replies (greetings, order status, returns).</Text>
              </Table.Td>
            </Table.Tr>
          )}
        </Table.Tbody>
      </Table>

      <AddModal opened={adding} onClose={() => setAdding(false)} onSaved={refresh} />
    </Stack>
  );
}

function AddModal({ opened, onClose, onSaved }: { opened: boolean; onClose: () => void; onSaved: () => void }) {
  const stores = useStores();
  const [title, setTitle] = useState("");
  const [storeId, setStoreId] = useState<string | null>(null); // null/"shared" = shared
  const [text, setText] = useState("");

  const storeOptions = [
    { value: "shared", label: "Shared (all stores)" },
    ...(stores.data ?? []).map((s) => ({ value: String(s.id), label: s.name })),
  ];
  const resolvedStore = storeId && storeId !== "shared" ? Number(storeId) : null;

  const save = useMutation({
    mutationFn: () => api("/canned", "POST", { title, store_id: resolvedStore, body_text: text }),
    onSuccess: () => {
      setTitle("");
      setText("");
      onSaved();
      onClose();
    },
  });

  const submit = (e: FormEvent) => {
    e.preventDefault();
    save.mutate();
  };

  return (
    <Modal opened={opened} onClose={onClose} title="Add canned response" size="lg">
      <form onSubmit={submit}>
        <Stack>
          {save.isError && <Alert color="red">{(save.error as Error).message}</Alert>}
          <TextInput label="Title" required value={title} onChange={(e) => setTitle(e.currentTarget.value)} />
          <Select
            label="Applies to"
            data={storeOptions}
            value={storeId ?? "shared"}
            allowDeselect={false}
            onChange={setStoreId}
          />
          <Textarea
            label="Response text"
            autosize
            minRows={5}
            maxRows={16}
            value={text}
            onChange={(e) => setText(e.currentTarget.value)}
          />
          <Group justify="flex-end">
            <Button variant="default" onClick={onClose}>
              Cancel
            </Button>
            <Button type="submit" disabled={!title.trim() || !text.trim()} loading={save.isPending}>
              Save
            </Button>
          </Group>
        </Stack>
      </form>
    </Modal>
  );
}
