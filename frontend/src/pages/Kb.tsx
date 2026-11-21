import {
  Alert,
  Badge,
  Button,
  FileInput,
  Group,
  Modal,
  SegmentedControl,
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

import { api, type KbDocument, useStores } from "../api";

export default function Kb() {
  const queryClient = useQueryClient();
  const docs = useQuery({ queryKey: ["kb"], queryFn: () => api<KbDocument[]>("/kb") });
  const stores = useStores();
  const [adding, setAdding] = useState(false);
  const refresh = () => queryClient.invalidateQueries({ queryKey: ["kb"] });

  const storeName = (id: number | null) => (id === null ? "Shared (all stores)" : stores.data?.find((s) => s.id === id)?.name ?? id);
  const toggle = useMutation({
    mutationFn: (d: KbDocument) => api(`/kb/${d.id}`, "PATCH", { is_active: !d.is_active }),
    onSuccess: refresh,
  });
  const remove = useMutation({ mutationFn: (id: number) => api(`/kb/${id}`, "DELETE"), onSuccess: refresh });

  return (
    <Stack>
      <Group justify="space-between">
        <Title order={3}>Knowledge base</Title>
        <Button onClick={() => setAdding(true)}>Add document</Button>
      </Group>
      <Text c="dimmed" size="sm">
        Policies and product information the AI uses to draft replies. Tag each to a store, or mark it shared.
      </Text>

      <Table>
        <Table.Thead>
          <Table.Tr>
            <Table.Th>Title</Table.Th>
            <Table.Th>Scope</Table.Th>
            <Table.Th>Size</Table.Th>
            <Table.Th>Status</Table.Th>
            <Table.Th />
          </Table.Tr>
        </Table.Thead>
        <Table.Tbody>
          {docs.data?.map((d) => (
            <Table.Tr key={d.id}>
              <Table.Td>
                {d.title}
                {d.filename && (
                  <Text span c="dimmed" size="xs">
                    {" "}
                    · {d.filename}
                  </Text>
                )}
              </Table.Td>
              <Table.Td>
                <Badge variant="light" color={d.store_id === null ? "grape" : "blue"}>
                  {storeName(d.store_id)}
                </Badge>
              </Table.Td>
              <Table.Td>{d.char_count.toLocaleString()} chars</Table.Td>
              <Table.Td>
                <Badge color={d.is_active ? "green" : "gray"}>{d.is_active ? "Active" : "Off"}</Badge>
              </Table.Td>
              <Table.Td ta="right">
                <Button size="compact-xs" variant="subtle" onClick={() => toggle.mutate(d)}>
                  {d.is_active ? "Turn off" : "Turn on"}
                </Button>
                <Button size="compact-xs" variant="subtle" color="red" onClick={() => remove.mutate(d.id)}>
                  Delete
                </Button>
              </Table.Td>
            </Table.Tr>
          ))}
          {docs.data?.length === 0 && (
            <Table.Tr>
              <Table.Td colSpan={5}>
                <Text c="dimmed">No documents yet. Add your warranty, returns and product policies.</Text>
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
  const [mode, setMode] = useState("text");
  const [title, setTitle] = useState("");
  const [storeId, setStoreId] = useState<string | null>(null); // null = shared
  const [text, setText] = useState("");
  const [file, setFile] = useState<File | null>(null);

  const storeOptions = [
    { value: "shared", label: "Shared (all stores)" },
    ...(stores.data ?? []).map((s) => ({ value: String(s.id), label: s.name })),
  ];
  const resolvedStore = storeId && storeId !== "shared" ? Number(storeId) : null;

  const save = useMutation({
    mutationFn: async () => {
      if (mode === "file") {
        const form = new FormData();
        form.append("file", file!);
        form.append("title", title);
        if (resolvedStore !== null) form.append("store_id", String(resolvedStore));
        const res = await fetch("/api/v1/kb/upload", { method: "POST", headers: { "X-Requested-With": "desk" }, body: form });
        if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail ?? "Upload failed");
        return;
      }
      await api("/kb", "POST", { title, store_id: resolvedStore, body_text: text });
    },
    onSuccess: () => {
      setTitle("");
      setText("");
      setFile(null);
      onSaved();
      onClose();
    },
  });

  const submit = (e: FormEvent) => {
    e.preventDefault();
    save.mutate();
  };
  const canSave = title.trim() && (mode === "text" ? text.trim() : file);

  return (
    <Modal opened={opened} onClose={onClose} title="Add knowledge-base document" size="lg">
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
          <SegmentedControl
            value={mode}
            onChange={setMode}
            data={[
              { value: "text", label: "Paste text" },
              { value: "file", label: "Upload file" },
            ]}
          />
          {mode === "text" ? (
            <Textarea
              label="Document text"
              autosize
              minRows={6}
              maxRows={16}
              value={text}
              onChange={(e) => setText(e.currentTarget.value)}
            />
          ) : (
            <FileInput
              label="File"
              description="PDF, Word (.docx), text, Markdown or CSV — up to 10 MB"
              accept=".pdf,.docx,.txt,.md,.csv,.json"
              value={file}
              onChange={setFile}
            />
          )}
          <Group justify="flex-end">
            <Button variant="default" onClick={onClose}>
              Cancel
            </Button>
            <Button type="submit" disabled={!canSave} loading={save.isPending}>
              Save
            </Button>
          </Group>
        </Stack>
      </form>
    </Modal>
  );
}
